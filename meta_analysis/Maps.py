'''
    TEST
'''
import scipy, copy, nilearn
import numpy as np
import nibabel as nib
import pandas as pd
from scipy.ndimage import gaussian_filter
from sklearn.covariance import LedoitWolf

from .globals import mem

from .tools import print_percent, index_3D_to_1D

import multiprocessing
from joblib import Parallel, delayed

# @profile
def compute_maps(df, **kwargs):
    '''
        Given a list of pmids, builds their activity maps (flattened in 1D) on a LIL sparse matrix format.
        Used for multiprocessing in get_all_maps_associated_to_keyword function.

        pmids : list of pmid
        Ni, Nj, Nk : size of the 3D box (used to flatten 3D to 1D indices)
        inv_affine : the affine inverse used to compute voxels coordinates

        Returns sparse LIL matrix of shape (len(pmids), Ni*Nj*Nk) containing all the maps
    '''
    Ni = kwargs['Ni']
    Nj = kwargs['Nj']
    Nk = kwargs['Nk']
    inv_affine = kwargs['inv_affine']
    index_dict = kwargs['index_dict']
    n_maps = kwargs['n_maps']
    mask = kwargs['mask']
    verbose = kwargs['verbose']

    col_names = kwargs['col_names']
    groupby_col = col_names['groupby']
    x_col = col_names['x']
    y_col = col_names['y']
    z_col = col_names['z']
    weight_col = col_names['weight']

    maps = scipy.sparse.lil_matrix((n_maps, Ni*Nj*Nk))

    n_tot = df.shape[0]
    for i_row, row in enumerate(zip(df[groupby_col], df[x_col], df[y_col], df[z_col], df[weight_col])):
        print_percent(i_row, n_tot, verbose=verbose)

        groupby_id, x, y, z, weight = row
        map_id = index_dict[groupby_id]

        i, j, k = np.clip(np.floor(np.dot(inv_affine, [x, y, z, 1]))[:-1].astype(int), [0, 0, 0], [Ni-1, Nj-1, Nk-1])
        
        if mask is None or mask[i, j, k] == 1:
            p = Maps.coord_to_id(i, j, k, Ni, Nj, Nk)
            maps[map_id, p] += weight

    return scipy.sparse.csr_matrix(maps)

# @mem.cache
def build_maps_from_df(df, col_names, Ni, Nj, Nk, affine, mask=None, verbose=False):
    '''
        Given a keyword, finds every related studies and builds their activation maps.

        reduce : integer, reducing scale factor. Ex : if reduce=2, aggregates voxels every 2 voxels in each direction.
                Notice that this affects the affine and box size.

        Returns 
            maps: sparse CSR matrix of shape (n_voxels, n_maps) containing all the related flattenned maps where
                    n_maps is the number of pmids related to the keyword
                    n_voxels is the number of voxels in the box (may have changed if reduce != 1)
            Ni_r, Nj_r, Nk_r: new box dimension (changed if reduce != 1)
            affine_r: new affine (changed if reduce != 1)
    '''

    # Creating map index
    unique_pmid = df['pmid'].unique()
    n_maps = len(unique_pmid)
    index_dict = {k:v for v, k in enumerate(unique_pmid)}

    # LIL format allows faster incremental construction of sparse matrix
    maps = scipy.sparse.csr_matrix((n_maps, Ni*Nj*Nk))

    # Multiprocessing maps computation
    n_jobs = multiprocessing.cpu_count()//2
    splitted_df= np.array_split(df, n_jobs, axis=0)

    kwargs = {
        'Ni': Ni,
        'Nj': Nj,
        'Nk': Nk,
        'inv_affine': np.linalg.inv(affine),
        'index_dict': index_dict,
        'n_maps': n_maps,
        'col_names': col_names,
        'mask': None if mask is None else mask.get_fdata(),
        'verbose': verbose
    }
    
    results = Parallel(n_jobs=n_jobs, backend='multiprocessing')(delayed(compute_maps)(sub_df, **kwargs) for sub_df in splitted_df)
    
    print('Merging...')
    for m in results:
        maps += m

    maps = maps.transpose()
    
    return maps

class Atlas:
    def __init__(self, atlas):
        self.atlas = None
        self.atlas_img = None
        self.data = None
        self.labels = None
        self.n_labels = None

        if atlas is None:
            return

        self.atlas = atlas
        self.atlas_img = nilearn.image.load_img(atlas['maps'])
        self.data = self.atlas_img.get_fdata()
        self.labels = atlas['labels']
        self.n_labels = len(self.labels)


class Maps:
    def __init__(self, df=None,
                       Ni=None, Nj=None, Nk=None,
                       affine=None,
                       mask=None,
                       atlas=None,
                       groupby_col=None,
                       x_col='x',
                       y_col='y',
                       z_col='z',
                       weight_col='weight',
                       save_memory=True,
                       verbose=False
                       ):
        '''


        Args:
            df (pandas.DataFrame): Pandas DataFrame containing the (x,y,z) coordinates, the weights and the map id. The names of the columns can be specified.
            Ni (int): X size of the bounding box.
            Nj (int): Y size of the bounding box.
            Nk (int): Z size of the bounding box.
            affine (numpy.ndarray): Array with shape (4, 4) storing the affine used to compute brain voxels coordinates from world cooridnates.
            mask (nibabel.Nifti1Image): Nifti1Image with 0 or 1 data.  0: outside the mask, 1: inside.
            atlas (Object): Object containing a nibabel.Nifti1Image or a path to it in atlas['maps'] and a list of the labels in atlas['labels']
            groupby_col (str): Name of the column on which the groupby operation is operated. Or in an equivalent way, the name of the column storing the ids of the maps.
            x_col (str): Name of the column storing the x coordinates.
            y_col (str): Name of the column storing the y coordinates.
            z_col (str): Name of the column storing the z coordinates.
            weight_col (str): Name of the column storing the weights. 
        '''

        self._Ni = Ni
        self._Nj = Nj
        self._Nk = Nk
        self._affine = affine
        self._save_memory = save_memory
        self._mask = mask
        self._maps = None
        self._n_voxels, self._n_maps = 0, 0
        self._atlas = Atlas(atlas)
        self._maps_dense =  None
        self._maps_atlas = None
        self._atlas_filter_matrix = None

        # Build maps
        if isinstance(df, pd.DataFrame):
            if groupby_col == None:
                raise ValueError('Must specify column name to group by maps.')

            if Ni is None or Nj is None or Nk is None or affine is None:
                raise ValueError('Must specify box size (Ni, Nj, Nk) and affine when initializing with pandas dataframe.')

            col_names = {
                'groupby': groupby_col,
                'x': x_col,
                'y': y_col,
                'z': z_col,
                'weight': weight_col
            }

            self._maps = build_maps_from_df(df, col_names, Ni, Nj, Nk, affine, mask, verbose)

        elif isinstance(df, tuple):
            self._maps = scipy.sparse.csr_matrix(df)

        elif isinstance(df, int):
            self._maps = scipy.sparse.csr_matrix((df, 1))

        elif df is None:
            self._maps = None

        else:
            raise ValueError('First argument not understood. Must be pandas df, int or length 2 tuple.')

        # Refresh infos according to new map
        if self._maps is not None:
            self._n_voxels, self._n_maps = self._maps.shape

        self._build_atlas_filter_matrix()
        self._refresh_atlas_maps()


    #_____________PROPERTIES_____________#
    @property
    def save_memory(self):
        return self._save_memory

    @save_memory.setter
    def save_memory(self, save_memory):
        self._save_memory = save_memory

        if save_memory:
            if hasattr(self, '_maps_dense'): del self._maps_dense
        else:
            self._set_dense_maps()

    @property
    def maps(self):
        return self._maps

    @maps.setter
    def maps(self, maps):
        if maps is None:
            self._n_voxels, self._n_maps = 0, 0

        else:
            self._n_voxels, self._n_maps = maps.shape

        self._maps = maps
        self._refresh_atlas_maps()

        if hasattr(self, '_save_memory') and not self._save_memory:
            self._set_dense_maps()

    #_____________CLASS_METHODS_____________#
    @classmethod
    def zeros(cls, shape, **kwargs):
        return cls(df=shape, **kwargs)

    #_____________PRIVATE_TOOLS_____________#
    def _copy_header(self, other):
        self._Ni = other._Ni
        self._Nj = other._Nj
        self._Nk = other._Nk
        self._affine = other._affine
        self._mask = other._mask
        self._save_memory = other._save_memory
        self._atlas = other._atlas

        return self

    def __str__(self):
        string = '\nMaps object containing {} maps.\n'
        string += '____________Header_____________\n'
        string += 'N Nonzero : {}\n'
        string += 'N voxels : {}\n'
        string += 'N pmids : {}\n'
        string += 'Box size : ({}, {}, {})\n'
        string += 'Affine :\n{}\n'
        string += 'Has atlas : {}\n'
        string += 'Map : \n{}\n'
        string += 'Atlas Map : \n{}\n'
        return string.format(self._n_maps, self.maps.count_nonzero(), self._n_voxels, self._n_maps, self._Ni, self._Nj, self._Nk, self._affine, self._has_atlas(), self.maps, self._maps_atlas)

    @staticmethod
    def coord_to_id(i, j, k, Ni, Nj, Nk):
        return np.ravel_multi_index((i, j, k), (Ni, Nj, Nk), order='F')

    @staticmethod
    def id_to_coord(id, Ni, Nj, Nk):
        return np.unravel_index(id, (Ni, Nj, Nk), order='F')

    @staticmethod
    def unflatten_array(self, array, Ni, Nj, Nk):
        return array.reshape((Ni, Nj, Nk), order='F')

    def _coord_to_id(self, i, j, k):
        return self.coord_to_id(i, j, k, self._Ni, self._Nj, self._Nk)

    def _id_to_coord(self, id):
        return self.id_to_coord(id, self._Ni, self._Nj, self._Nk)

    def _flatten_array(self, array, _2D=False):
        shape = (-1, 1) if _2D else -1
        return array.reshape(shape, order='F')

    def _unflatten_array(self, array):
        return self.unflatten_array(array, self._Ni, self._Nj, self._Nk)

    def _build_atlas_filter_matrix(self):
        if not self._has_atlas():
            return

        atlas_data = self._flatten_array(self._atlas.data)
        filter_matrix = scipy.sparse.lil_matrix((self._atlas.n_labels, self._n_voxels))

        for k in range(self._atlas.n_labels):
            row = atlas_data == k
            filter_matrix[k, row] = 1

        return scipy.sparse.csr_matrix(filter_matrix)

    def _refresh_atlas_maps(self):
        if not self._has_atlas():
            return

        if self._atlas_filter_matrix is None:
            self._atlas_filter_matrix = self._build_atlas_filter_matrix()

        self._maps_atlas = self._atlas_filter_matrix.dot(self.maps)

    def _has_atlas(self):
        return self._atlas is not None and self._atlas.atlas is not None

    def _has_mask(self):
        return self._mask is not None and isinstance(self._mask, nib.Nifti1Image)

    def _get_maps(self, atlas):
        return self._maps_atlas if atlas else self._maps

    def _set_dense_maps(self):
        if self._maps is None:
            self._maps_dense = None
        else:
            self._maps_dense = self._maps.toarray().reshape((self._Ni, self._Nj, self._Nk, self._n_maps), order='F')

    #_____________OPERATORS_____________#

    def __iadd__(self, val):
        self.maps += val.maps
        return self

    def __add__(self, other):
        result = copy.copy(self)
        result += other
        return result

    def __imul__(self, val):
        self.maps *= val
        return self

    def __mul__(self, val):
        result = copy.copy(self)
        result *= val
        return result

    def __getitem__(self, key):
        return self.maps[:, key]

    #_____________DATA_TRANSFORMERS_____________#
    @staticmethod
    def map_to_array(map, Ni, Nj, Nk):
        '''
            Convert a sparse matrix of shape (n_voxels, 1) into a dense 3D numpy array of shape (Ni, Nj, Nk).

            Indexing of map is supposed to have been made Fortran like (first index moving fastest).
        '''
        n_voxels, _ = map.shape

        if n_voxels != Ni*Nj*Nk:
            raise ValueError('Map\'s length ({}) does not match given box ({}, {}, {}) of size {}.'.format(n_voxels, Ni, Nj, Nk, Ni*Nj*Nk))

        return Maps.unflatten_array(map.toarray(), Ni, Nj, Nk)

    @staticmethod
    def array_to_map(array):
        return scipy.sparse.csr_matrix(Maps._flatten_array(array, _2D=True))

    @staticmethod
    def array_to_img(array, affine):
        '''
            Convert a dense 3D array into a nibabel Nifti1Image.
        '''
        return nib.Nifti1Image(array, affine)

    @staticmethod
    def map_to_img(map, Ni, Nj, Nk, affine):
        '''
            Convert a sparse matrix of shape (n_voxels, 1) into a nibabel Nifti1Image.

            Ni, Nj, Nk are the size of the box used to index the flattened map matrix.
        '''
        return Maps.array_to_img(Maps.map_to_array(map, Ni, Nj, Nk), affine)

    def to_array(self, map_id=None):
        '''
            Convert one map into a 3D numpy.ndarray.

            Args:
                map_id (int, optional): If int : id of the map to convert. 
                    If None, convert the unique contained map, if several maps, raise an error. Defaults to None. 
        
            Returns:
                (numpy.ndarray) 3D array containing the chosen map information.

            Raises:
                KeyError: If map_id is None and the instance contains more than one map. 
        '''
        if map_id is not None:
            return self.map_to_array(self._maps[:, map_id], self._Ni, self._Nj, self._Nk)

        if self._n_maps > 1:
            raise KeyError('This Maps object contains {} maps, specify which map to convert to array.'.format(self._n_maps))

        return self.map_to_array(self._maps[:, 0], self._Ni, self._Nj, self._Nk)

    def to_img(self, map_id=None):
        '''
            Convert one map into a nibabel.Nifti1Image.

            Args:
                map_id (int, optional): If int : id of the map to convert. 
                    If None, convert the unique contained map, if several maps, raise an error. Defaults to None. 
        
            Returns:
                (nibabel.Nifti1Image) Nifti1Image containing the chosen map information.

            Raises:
                KeyError: If map_id is None and the instance contains more than one map. 
        '''
        if map_id is not None:
            return self.map_to_img(self._maps[:, map_id], self._Ni, self._Nj, self._Nk, self._affine)

        if self._n_maps > 1:
            raise KeyError('This Maps object contains {} maps, specify which map to convert to img.'.format(self._n_maps))

        return self.map_to_img(self._maps[:, 0], self._Ni, self._Nj, self._Nk, self._affine)

    def to_array_atlas(self, map_id=None, ignore_bg=True):
        '''
            Convert one atlas map into a 3D numpy.array.

            Args:
                map_id (int, optional): If int : id of the map to convert. 
                    If None, converts the unique contained atlas map, if several atlas maps, raise an error. Defaults to None.
                ignore_bg (bool, optional): If True: ignore the first label of the atlas (background) which is set to 0 in the returned array.

            Returns:
                (numpy.ndarray) 3D array containing the chosen atlas map information.

            Raises:
                AttributeError: If no atlas has been given to this instance.
                KeyError: If map_id is None and the instance contains more than one map. 
        '''
        if not self._has_atlas():
            raise AttributeError('No atlas were given.')

        if map_id is None and self._n_maps > 1:
            raise KeyError('This Maps object contains atlas {} maps, specify which map to convert to array.'.format(self._n_maps))
        elif map_id is None:
            map_id = 0

        array = np.zeros((self._Ni, self._Nj, self._Nk))
        start = 1 if ignore_bg else 0

        for k in range(start, self._atlas.n_labels):
            array[self._atlas.data == k] = self._maps_atlas[k, map_id]

        return array

    def to_img_atlas(self, map_id=0, ignore_bg=True):
        '''
            Convert one atlas map into a nibabel.Nifti1Image.

            Args:
                map_id (int, optional): If int : id of the map to convert. 
                    If None, converts the unique contained atlas map, if several atlas maps, raise an error. Defaults to None.
                ignore_bg (bool, optional): If True: ignore the first label of the atlas (background) which is set to 0 in the returned array.

            Returns:
                (nibabel.Nifti1Image) Nifti1Image containing the chosen atlas map information.

            Raises:
                AttributeError: If no atlas as been given to this instance.
                KeyError: If map_id is None and the instance contains more than one map. 
        '''
        return self.array_to_img(self.to_array_atlas(map_id=map_id, ignore_bg=ignore_bg), self._affine)

    #_____________PUBLIC_TOOLS_____________#
    def apply_mask(self, mask):
        '''
            Set the contribution of every voxels outside the mask to zero.

            Args:
                mask (nibabel.Nifti1Image): Nifti1Image with 0 or 1 array. 0: outside the mask, 1: inside.
        '''
        mask_array = self._flatten_array(mask.get_fdata())
        filter_matrix = scipy.sparse.diags(mask_array, format='csr')

        self.maps = filter_matrix.dot(self.maps)
        self._mask = mask

    def randomize(self, n_peaks, n_maps, p=None, use_mask=False, inplace=False):
        '''
            Randomize the maps by sampling n_peaks of weight 1 (may overlap) over n_maps maps.

            Args:
                n_peaks (int): Number of peaks to sample maps-wise. Each peak has a weight 1 and the weights of the peaks sampled on the same voxel of the same map are added.
                n_maps (int): Number of maps on which to perform the sample.
                p (Maps instance or np.ndarray): (Optional) Distribution of probability of the peaks over the voxels.
                    The distribution may be given either by a Maps instance containing 1 map or a np.ndarray of same shape as the box of the current Maps instance (Ni, Nj, Nk).
                    If None, sample uniformly accros the box. Default: None
                use_mask (bool): (Optional) If True, use the mask given when initializing the Maps object.
                    Important : the given distribution p is then shrinked and re-normalized.
                    If False p is unchanged. Default : False.
                inplace (bool): (Optional) Performs the sampling inplace (True) or creates a new instance (False). Default False.

            Returns:
                (Maps instance) Self or a copy depending on inplace.
        '''
        if self._Ni is None or self._Nj is None or self._Nk is None:
            raise ValueError('Invalid box size ({}, {}, {}).'.format(Ni, Nj, Nk))

        n_voxels = self._Ni*self._Nj*self._Nk

        if p is None:
            p = np.ones(n_voxels)/n_voxels
        
        elif isinstance(p, Maps):
            if p.n_maps != 1:
                raise ValueError('Maps object should contain exactly one map to serve as distribution. Given has {} maps.'.format(p.n_maps))
            p = p.maps.transpose().toarray()[0]

        elif isinstance(p, np.ndarray):
            if p.shape != (self._Ni, self._Nj, self._Nk):
                raise ValueError('Invalid numpy array to serve as a distribution. Should be of shape ({}, {}, {}).'.format(self._Ni, self._Nj, self._Nk))
            p = self._flatten_array(p)

        else:
            raise ValueError('Invalid distribution p. Must be either None, Maps object or numpy.ndarray.')

        if use_mask:
            if not self._has_mask():
                raise AttributeError('No mask found. Consider to apply mask.')

            mask = self._flatten_array(mask.get_fdata())
            p = np.ma.masked_array(p, np.logical_not(mask))
            p /= np.sum(p)

        maps = scipy.sparse.dok_matrix((n_voxels, n_maps))
        voxels_samples = np.random.choice(n_voxels, size=n_peaks, p=p)
        voxels_samples_unique, counts = np.unique(voxels_samples, return_counts=True)

        for i in range(len(voxels_samples_unique)):
            map_id = np.random.randint(n_maps)
            maps[voxels_samples_unique[i], map_id] = counts[i]

        maps = scipy.sparse.csr_matrix(maps)


        new_maps = self if inplace else copy.copy(self)
        new_maps.maps = maps

        return new_maps

    def normalize(self, inplace=False):
        '''
            Normalize each maps separatly so that each maps sums to 1.

            Args:
                inplace (bool, optional): If True performs the normalization inplace else create a new instance.

            Returns:
                (Maps) Self or a copy depending on inplace.
        '''
        diag = scipy.sparse.diags(np.power(self.n_peaks(atlas=False), -1))

        new_maps = self if inplace else copy.copy(self)
        new_maps.maps = self._maps.dot(diag)

        if self._has_atlas():
            diag_atlas = scipy.sparse.diags(np.power(self.n_peaks(atlas=True), -1))
            new_maps._maps_atlas = self._maps_atlas.dot(diag_atlas)

        return new_maps

    @staticmethod
    def _smooth_array(array, sigma):
        return gaussian_filter(array, sigma=sigma)

    @staticmethod
    def _smooth_map(map, sigma, Ni, Nj, Nk):
        array = Maps.map_to_array(map, Ni, Nj, Nk)
        array = self._smooth_array(array, sigma=sigma)
        map = Maps.array_to_map(array)
        return map

    def smooth(self, sigma, map_id=None, inplace=False, verbose=False):
        '''
            Convolve chosen maps with gaussian kernel.

            Args:
                sigma (float): Standard deviation of the gaussian kernel.
                map_id (int, optional): If None: convolves each maps. If int: convolves only the chosen map. Defaults to None.
                inplace (bool, optional): If True performs the normalization inplace else create a new instance. Defaults to False
                verbose (bool, optional): If True print logs.

        '''
        lil_maps = scipy.sparse.lil_matrix(self.maps)

        if map_id is None:
            map_ids = range(self._n_maps)
        else:
            map_ids = [map_id]

        for k in map_ids:
            if verbose: print('Smoothing {} out of {}.'.format(k+1, self._n_maps))
            array = self.to_array(k)
            array = self._smooth_array(array, sigma=sigma)
            lil_maps[:, k] = self._flatten_array(array, _2D=True)

        csr_maps = scipy.sparse.csr_matrix(lil_maps)

        new_maps = self if inplace else copy.copy(self)
        new_maps.maps = csr_maps
        if new_maps._has_atlas():
            new_maps.refresh_atlas_maps()
        return new_maps

    #_____________STATISTICS_____________#
    def n_peaks(self, atlas=False):
        '''
            Compute the sum of weights in each maps (equivalent to number of peaks if unit weights are 1).

            Args:
                atlas (bool, optional): If True, the atlas maps are considered.
        '''
        maps = self._get_maps(atlas=atlas)

        e = scipy.sparse.csr_matrix(np.ones(maps.shape[0]))
        return np.array(e.dot(maps).toarray()[0])

    def max(self, atlas=False, **kwargs):
        '''
            Compute the maximum. axis=None: element-wise, axis=0: maps-wise, axis=1: voxels/labels-wise.

            Args:
                atlas (bool, optional): If True, the atlas maps are considered.
                **kwargs: Kwargs are passed to scipy.sparse.csr_matrix.max() function.

        '''
        maps = self._get_maps(atlas=atlas)
        return maps.max()

    def sum(self, atlas=False, **kwargs):
        '''
            Compute the sum. axis=None: element-wise, axis=0: maps-wise, axis=1: voxels/labels-wise.

            Args:
                atlas (bool, optional): If True, the atlas maps are considered.
                **kwargs: Kwargs are passed to scipy.sparse.csr_matrix.sum() function.

        '''
        maps = self._get_maps(atlas=atlas)
        return maps.sum(**kwargs)

    def summed_map(self):
        '''
            Sums all maps.

            Returns:
                (Maps) New Maps instance containing the summed map.
        '''
        sum_map = Maps()
        sum_map._copy_header(self)
        sum_map.maps = scipy.sparse.csr_matrix(self.sum(axis=1))
        sum_map._maps_atlas = scipy.sparse.csr_matrix(self.sum(atlas=True, axis=1))

        return sum_map

    @staticmethod
    def _average(maps):
        '''
            Computes the average map of the given maps on the second axis.

            maps : sparse CSR matrix of shape (n_voxels, n_maps) where
                n_voxels is the number of voxels in the box
                n_maps is the number of pmids

            Returns a sparse CSR matrix of shape (n_voxels, 1) representing the flattened average map.
        '''
        _, n_maps = maps.shape
        e = scipy.sparse.csr_matrix(np.ones(n_maps)/n_maps).transpose()

        return maps.dot(e)


    @staticmethod
    def _variance(maps, bias=False):
        '''
            Computes the variance map of the given maps on the second axis.

            Returns a sparse CSR matrix of shape (n_voxels, 1) representing the flattened variance map.
        '''
        _, n_maps = maps.shape

        avg_map = Maps._average(maps)
        maps_squared = maps.multiply(maps) # Squared element wise

        avg_squared_map = Maps._average(maps_squared)
        squared_avg_map = avg_map.multiply(avg_map)

        var = avg_squared_map - squared_avg_map

        if not bias:
            var *= (n_maps/(n_maps-1))

        return var

    def avg(self):
        '''
            Computes the average map.

            Returns:
                (Maps) New Maps instance containing the average map.
        '''
        avg_map = Maps()
        avg_map._copy_header(self)
        avg_map.maps = self._average(self.maps)
        avg_map._maps_atlas = self._average(self._maps_atlas)

        return avg_map

    def var(self, bias=True):
        '''
            Computes the variance map.

            Args:
                bias (bool, optional): If True, computes the biased variance (1/n_maps factor), else compute the unbiased variance (1/(n_maps-1) factor).

            Returns:
                (Maps) New Maps instance containing the variance map.
        '''
        var_map = Maps()
        var_map._copy_header(self)
        var_map.maps = self._variance(self._maps, bias=bias)
        var_map._maps_atlas = self._variance(self._maps_atlas, bias=bias)

        return var_map

    def cov(self, atlas=True, bias=False, shrink=None, sparse=False, ignore_bg=True):
        '''
            Computes the empirical covariance matrix of the voxels, the observations being the different maps.
            Important : Considering covariance between atlas' labels (atlas=True) instead of voxels is highly recommended
            since the number of voxels is often huge (~1 million), the covariance matrix would be of big shape (1 million, ~1 million) and the computation will probably not finish.

            Args:
                atlas (bool, optional): If True, consider covariance between atlas' labels. If False, covariance between voxels. Default is True (recommended).
                bias (bool, optional): If True, computes the biased covariance, else unbiased. Defaults to False.
                shrink(str, optional): Shrink the covariance matrix. If 'LW', the LedoitWolf method is applied. Default is None.
                sparse(bool, optional): If False, converts the sparse covariance matrix to a dense array. Else, let it sparse. Default is False
                ignore_bg(bool, optional): If True, ignore the first label of the atlas (background).

            Returns:
                (numpy.ndarray or scipy.sparse.csr_matrix) A 2D matrix (sparse or dense depending on sparse parameter) of shape (n_voxels, n_voxels) representing the covariance matrix.
        '''
        if not self._has_atlas():
            raise ValueError('No atlas. Must specify an atlas when initializing Maps or specify atlas=False in cov() function.')

        if not bias and self._n_maps <= 1:
            raise ValueError('Unbiased covariance computation requires at least 2 maps ({} given).'.format(self._n_maps))

        maps = self._get_maps(atlas=atlas)
        ddof = 0 if bias else 1

        if atlas:
            labels = self._atlas.labels
            if ignore_bg:
                maps[0, :] = 0
                labels = labels[1:]


        e1 = scipy.sparse.csr_matrix(np.ones(self._n_maps)/(self._n_maps-ddof)).transpose()
        e2 = scipy.sparse.csr_matrix(np.ones(self._n_maps)/(self._n_maps)).transpose()

        M1 = maps.dot(e1)
        M2 = maps.dot(e2)
        M3 = maps.dot(maps.transpose())/((self._n_maps-ddof))

        # Empirical covariance matrix
        S =  M3 - M1.dot(M2.transpose())

        if not sparse:
            S = S.toarray()

        if shrink == 'LW':
            S = LedoitWolf().fit(S.toarray()).covariance_

        return S, labels if atlas else S


    def iterative_smooth_avg_var(self, compute_var=True, sigma=None, bias=False, verbose=False):
        '''
            Compute average and variance of the maps in self.maps (previously smoothed if sigma!=None) iteratively.
            (Less memory usage).
        '''

        if not compute_var:
            return self.avg().smooth(sigma=sigma), None

        current_map = self[0] if self.save_memory else self._maps_dense[:, :, :, 0]
        if sigma != None:
                if self.save_memory:
                    current_map = self._smooth_map(current_map, sigma, self._Ni, self._Nj, self._Nk)
                else:
                    current_map = self._smooth_array(current_map, sigma)

        avg_map_n = copy.copy(current_map)
        var_map_n = Maps.zeros(self._n_voxels).maps if self.save_memory else np.zeros((self._Ni, self._Nj, self._Nk))

        for k in range(2, self._n_maps+1):
            if verbose:
                print('Iterative smooth avg var {} out of {}...'.format(k, self._n_maps), end='\r', flush=True)
            avg_map_p, var_map_p = copy.copy(avg_map_n), copy.copy(var_map_n)
            current_map = self[k-1] if self.save_memory else self._maps_dense[:, :, :, k-1]

            if sigma != None:
                if self.save_memory:
                    current_map = self._smooth_map(current_map, sigma, self._Ni, self._Nj, self._Nk)
                else:
                    current_map = self._smooth_array(current_map, sigma)

            avg_map_n = 1./k*((k-1)*avg_map_p + current_map)

            if bias:
                if self.save_memory:
                    var_map_n = (k-1)/(k)*var_map_p + (k-1)/(k)*(avg_map_p - avg_map_n).power(2) + 1./(k)*(current_map-avg_map_n).power(2)
                else:
                    var_map_n = (k-1)/(k)*var_map_p + (k-1)/(k)*np.power(avg_map_p - avg_map_n, 2) + 1./(k)*np.power(current_map-avg_map_n, 2)

            else:
                if self.save_memory:
                    var_map_n = (k-2)/(k-1)*var_map_p + (avg_map_p - avg_map_n).power(2) + 1./(k-1)*(current_map-avg_map_n).power(2)
                else:
                    var_map_n = (k-2)/(k-1)*var_map_p + np.power(avg_map_p - avg_map_n, 2) + 1./(k-1)*np.power(current_map-avg_map_n, 2)

        avg = Maps()._copy_header(self)
        var = Maps()._copy_header(self)

        avg.maps = avg_map_n if self.save_memory else self.array_to_map(avg_map_n)
        var.maps = var_map_n if self.save_memory else self.array_to_map(var_map_n)

        if verbose:
            print('Iterative smooth avg var {} out of {}... Done'.format(self._n_maps, self._n_maps))

        return avg, var

