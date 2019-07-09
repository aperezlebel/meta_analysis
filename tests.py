import unittest
from hypothesis import given, settings
import hypothesis.strategies as strats
import numpy as np
import scipy
import copy

from tools import index_3D_to_1D_checked, index_1D_to_3D_checked
from activity_map import Maps

@strats.composite
def random_permitted_case_3D(draw):
    Ni = draw(strats.integers(min_value=1))
    Nj = draw(strats.integers(min_value=1))
    Nk = draw(strats.integers(min_value=1))

    i = draw(strats.integers(min_value=0, max_value=Ni))
    j = draw(strats.integers(min_value=0, max_value=Nj))
    k = draw(strats.integers(min_value=0, max_value=Nk))

    return i, j, k, Ni, Nj, N

@strats.composite
def random_permitted_case_1D(draw):
    Ni = draw(strats.integers(min_value=1))
    Nj = draw(strats.integers(min_value=1))
    Nk = draw(strats.integers(min_value=1))

    p = draw(strats.integers(min_value=0, max_value=Ni*Nj*Nk-1))

    return p, Ni, Nj, Nk

class TestIndexesChange(unittest.TestCase):
    def test_empty(self):
        self.assertRaises(ValueError, index_3D_to_1D_checked, 0, 0, 0, 0, 0, 0)
        self.assertRaises(ValueError, index_1D_to_3D_checked, 0, 0, 0, 0)

    def test_one_empty(self):
        self.assertRaises(ValueError, index_3D_to_1D_checked, 0, 0, 0, 0, 1, 1)
        self.assertRaises(ValueError, index_1D_to_3D_checked, 0, 0, 1, 1)

    @given(Ni=strats.integers(min_value=1),
           Nj=strats.integers(min_value=1),
           Nk=strats.integers(min_value=1))
    def test_edge_cases(self, Ni, Nj, Nk):
        self.assertEqual(index_3D_to_1D_checked(0, 0, 0, Ni, Nj, Nk), 0)
        self.assertEqual(index_1D_to_3D_checked(0, Ni, Nj, Nk), (0, 0, 0))

        self.assertEqual(index_3D_to_1D_checked(Ni-1, Nj-1, Nk-1, Ni, Nj, Nk), Ni*Nj*Nk-1)
        self.assertEqual(index_1D_to_3D_checked(Ni*Nj*Nk-1, Ni, Nj, Nk), (Ni-1, Nj-1, Nk-1))

    @given(args = random_permitted_case_3D())
    def test_main_cases(self, args):
        i, j, k, Ni, Nj, Nk = args
        p = index_3D_to_1D_checked(i, j, k, Ni, Nj, Nk)
        self.assertEqual(index_1D_to_3D_checked(p, Ni, Nj, Nk), (i, j, k))

    @given(args = random_permitted_case_1D())
    def test_main_cases(self, args):
        p, Ni, Nj, Nk = args
        i, j, k = index_1D_to_3D_checked(p, Ni, Nj, Nk)
        self.assertEqual(index_3D_to_1D_checked(i, j, k, Ni, Nj, Nk), p)


@strats.composite
def random_maps(draw):
    Ni = draw(strats.integers(min_value=1, max_value=10))
    Nj = draw(strats.integers(min_value=1, max_value=10))
    Nk = draw(strats.integers(min_value=1, max_value=10))
    n_maps = draw(strats.integers(min_value=1, max_value=5))
    n_peaks = draw(strats.integers(min_value=1000, max_value=100000))

    maps = Maps(Ni=Ni, Nj=Nj, Nk=Nk).randomize(n_peaks, n_maps, inplace=True)
    return maps, Ni, Nj, Nk

class TestStatsComputation(unittest.TestCase):
    @given(args = random_maps())
    @settings(max_examples=100, deadline=1000)
    def test_avg(self, args):
        maps, Ni, Nj, Nk = args
        avg = maps.avg()
        self.assertTrue(np.allclose(avg.maps.toarray(), np.mean(maps.maps, axis=1)))

    @given(args = random_maps())
    @settings(max_examples=100, deadline=1000)
    def test_var(self, args):
        maps, Ni, Nj, Nk = args
        var = maps.var()
        arr1 = var.maps.transpose().toarray()
        arr2 = np.var(maps.maps.toarray(), axis=1)

        self.assertTrue(np.allclose(arr1, arr2))

class TestIterativeAvgVar(unittest.TestCase):
    @given(Ni=strats.integers(min_value=1, max_value=100),
           Nj=strats.integers(min_value=1, max_value=100),
           Nk=strats.integers(min_value=1, max_value=100),
           n_maps=strats.integers(min_value=1, max_value=10),
           )
    @settings(max_examples=100, deadline=1000)
    def test_null_maps(self, Ni, Nj, Nk, n_maps):
        maps = Maps.zeros((Ni*Nj*Nk, n_maps))

        avg, var = maps.iterative_smooth_avg_var()

        self.assertEqual(avg.maps.nnz, 0)
        self.assertEqual(var.maps.nnz, 0)

    @given(Ni=strats.integers(min_value=1, max_value=100),
           Nj=strats.integers(min_value=1, max_value=100),
           Nk=strats.integers(min_value=1, max_value=100),
           n_maps=strats.integers(min_value=1, max_value=10),
           )
    @settings(max_examples=100, deadline=1000)
    def test_constant_map(self, Ni, Nj, Nk, n_maps):
        maps = Maps.zeros((Ni*Nj*Nk, n_maps))
        maps.maps = scipy.sparse.csr_matrix(np.ones((Ni*Nj*Nk, n_maps)))

        avg, var = maps.iterative_smooth_avg_var()

        self.assertTrue(np.allclose(np.ones((Ni*Nj*Nk, 1)), avg.maps.toarray()))
        self.assertEqual(var.maps.nnz, 0)

    @given(args = random_maps())
    @settings(max_examples=100, deadline=1000)
    def test_without_smoothing(self, args):
        maps, Ni, Nj, Nk = args

        expected_avg_map = maps.avg().maps
        expected_var_map = maps.var().maps

        avg_map, var_map = maps.iterative_smooth_avg_var()

        arr1 = expected_var_map.toarray()
        arr2 = var_map.maps.toarray()
        # print('Arr1 \n{}'.format(arr1))
        # print('Arr 2 \n{}'.format(arr2))

        self.assertTrue(np.allclose(expected_avg_map.toarray(), avg_map.maps.toarray()))
        self.assertTrue(np.allclose(arr1, arr2))

    # @given(args = random_maps(),
    #        sigma = strats.floats(min_value=0.1, max_value=10.))
    # @settings(max_examples=100, deadline=1000)
    # def test_with_smoothing(self, args, sigma):
    #     maps, Ni, Nj, Nk = args

    #     maps_aux = copy.copy(maps)
    #     smoothed_maps = maps.smooth(sigma=sigma)

    #     expected_avg = smoothed_maps.avg()
    #     # expected_var_map = smoothed_maps.var().maps
    #     # print(maps)
    #     avg, var = maps_aux.iterative_smooth_avg_var(sigma=sigma)


    #     print('Arr 1 :\n{}'.format(expected_avg))
    #     print('Arr 2 :\n{}'.format(avg))
    #     # arr1 = expected_var_map.toarray()
    #     # arr2 = var_map.maps.toarray()
    #     # print('Arr1 \n{}'.format(arr1))
    #     # print('Arr 2 \n{}'.format(arr2))

    #     self.assertTrue(np.allclose(expected_avg.maps.toarray(), avg.maps.toarray()))
    #     # self.assertTrue(np.allclose(arr1, arr2))




if __name__ == '__main__':
    unittest.main()