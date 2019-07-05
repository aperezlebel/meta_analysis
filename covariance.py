from time import time
import numpy as np
import scipy
import seaborn as sns
from nilearn import plotting
from matplotlib import pyplot as plt
import multiprocessing
from joblib import Parallel, delayed

from globals import mem, coordinates, corpus_tfidf, Ni, Nj, Nk, affine, inv_affine
from builds import encode_pmid, encode_feature, decode_pmid, decode_feature
from tools import print_percent, empirical_cov_matrix, map_to_data, data_to_img, map_to_img
from threshold import estimate_threshold_covariance
from activity_map import get_all_maps_associated_to_keyword, plot_activity_map

# def empirical_cov_matrix(observations):
#     n_observations = observations.shape[0]

#     s_X = scipy.sparse.csr_matrix(observations)
#     s_Ones = scipy.sparse.csr_matrix(np.ones(n_observations))

#     M1 = s_X.transpose().dot(s_X)
#     M2 = (s_Ones.dot(s_X)).transpose()
#     M3 = s_Ones.dot(s_X)

#     return M1/n_observations - M2.dot(M3)/(n_observations**2)

def average_activity_map(keyword, reduce=1):
    '''
        Builds the average map of the studies related to the keyword.

        Returns a sparse CSR matrix of shape (n_pmids, 1) representing the flattened average map
        where n_pmids is the number of pmid related to the keyword.
    '''
    maps, Ni_r, Nj_r, Nk_r, affine_r = get_all_maps_associated_to_keyword(keyword, reduce=reduce)
    print(maps.shape)
    _, n_pmids = maps.shape
    e = scipy.sparse.csr_matrix(np.ones(n_pmids)/n_pmids).transpose()
    print(e.shape)

    return maps.dot(e), Ni_r, Nj_r, Nk_r, affine_r

def compute_map(pmid_enumerate, n_voxels, Ni_r, Nj_r, Nk_r, inv_affine_r, gaussian_filter):
    n_tot = len(pmid_enumerate)
    observations_ = np.zeros((n_tot, n_voxels))
    counts = np.zeros(n_tot).astype(int)
    p = 0
    for obs_index, pmid in pmid_enumerate:
        stat_img_data = np.zeros((Ni_r,Nj_r,Nk_r)) # Building blank stat_img with MNI152's shape

        # For each coordinates found in pmid (in mm), compute its corresponding voxels coordinates
        # and note it as activated
        print_percent(p, n_tot, prefix='Covariance matrix ')
        for index, row in coordinates.loc[coordinates['pmid'] == pmid].iterrows():
            x, y, z = row['x'], row['y'], row['z']
            i, j, k = np.minimum(np.floor(np.dot(inv_affine_r, [x, y, z, 1]))[:-1].astype(int), [Ni_r-1, Nj_r-1, Nk_r-1])
            stat_img_data[i, j, k] += 1
            counts[p] += 1

        # With gaussian kernel, sparse calculation may not be efficient (not enough zeros)
        if gaussian_filter:
            stat_img_data = scipy.ndimage.gaussian_filter(stat_img_data, sigma=sigma)

        observations_[p, :] = stat_img_data.reshape(-1)
        p += 1

    return observations_, counts

@mem.cache
def build_covariance_matrix_from_keyword(keyword, gaussian_filter=False, sigma=2., reduce=2):
    '''
        Build empirical covariance matrix of the voxel of the activity map associated to the given keyword
    '''
    feature_id = encode_feature[keyword]
    nonzero_pmids = np.array([int(decode_pmid[index]) for index in corpus_tfidf[:, feature_id].nonzero()[0]])

    # Change box size according to reduce factor
    Ni_r, Nj_r, Nk_r = np.ceil(np.array((Ni, Nj, Nk))/reduce).astype(int)

    # stat_img_data = np.zeros((Ni_r,Nj_r,Nk_r)) # Building blank stat_img with MNI152's shape
    n_observations = len(nonzero_pmids)
    n_voxels = Ni_r*Nj_r*Nk_r

    coords = np.zeros((Ni_r, Nj_r, Nk_r, 3)).astype(int)

    for k in range(Ni_r):
         coords[k, :, :, 0] = k
    for k in range(Nj_r):
         coords[:, k, :, 1] = k
    for k in range(Nk_r):
         coords[:, :, k, 2] = k

    coords = coords.reshape(-1, 3)

    # Change affine to new box size
    affine_r = np.copy(affine)
    for i in range(3):
        affine_r[i, i] = affine[i, i]*reduce

    inv_affine_r = np.linalg.inv(affine_r)

    n_jobs = multiprocessing.cpu_count()-1
    splitted_array = np.array_split(np.array(list(enumerate(nonzero_pmids))), n_jobs)
    observations, counts = zip(*Parallel(n_jobs=n_jobs, backend='multiprocessing')(delayed(compute_map)(sub_array, n_voxels, Ni_r, Nj_r, Nk_r, inv_affine_r, gaussian_filter) for sub_array in splitted_array))

    observations = np.concatenate(observations, axis=0)
    counts = np.concatenate(counts, axis=0)
    
    avg_n_peaks = np.mean(counts)

    # Sparse computation of covariance matrix
    # s_X = scipy.sparse.csr_matrix(observations)
    # s_Ones = scipy.sparse.csr_matrix(np.ones(n_observations))

    # M1 = s_X.transpose().dot(s_X)
    # M2 = (s_Ones.dot(s_X)).transpose()
    # M3 = s_Ones.dot(s_X)

    # s_cov_matrix = M1/n_observations - M2.dot(M3)/(n_observations**2)
    s_cov_matrix = empirical_cov_matrix(observations)

    return s_cov_matrix, coords, affine_r, avg_n_peaks

def plot_matrix_heatmap(M):
    sns.heatmap(M)
    plt.show()

def plot_cov_matrix_brain(M, coords, affine, threshold):
    coords_world = np.zeros(coords.shape)

    # print(affine)

    for k in range(coords.shape[0]):
        coords_world[k, :] = np.dot(affine, [coords[k, 0], coords[k, 1], coords[k, 2], 1])[:-1]
        # print(coords_world[k, :])

    # threshold = np.max(M)*0.1
    # print(threshold)
    plotting.plot_connectome(M, coords_world, node_size=5, node_color='black', edge_threshold=threshold)
    plt.show()


def variance_activity_map_by_keyword(keyword, sigma=2.):
    pass



if __name__ == '__main__':
    keyword = 'memory'
    # keyword = 'prosopagnosia'
    sigma = 2.
    reduce = 10

    # cov_matrix, coords, affine_r, avg_n_peaks = build_covariance_matrix_from_keyword(keyword, sigma=sigma, reduce=reduce, gaussian_filter=False)
    # print(cov_matrix)
    # print(cov_matrix.shape)

    # cov_array = cov_matrix.toarray() 
    # # print(np.percentile(cov_array, .9999))
    # # print(len(cov_array[cov_array > 0]))
    # # plot_matrix_heatmap(cov_array)

    # threshold = estimate_threshold_covariance(avg_n_peaks, Ni//reduce, Nj//reduce, Nk//reduce, N_simulations=1000, sigma=sigma, apply_gaussian_filter=False)
    # print('Avg peaks : {}'.format(avg_n_peaks))
    # print('Threshold : {}'.format(threshold))
    # print('Plotting')
    # # threshold = '25%'
    # plot_cov_matrix_brain(cov_array, coords, affine_r, threshold)

    avg_map, Ni, Nj, Nk, affine = average_activity_map(keyword)
    print(avg_map.shape)

    avg_data = map_to_data(avg_map, Ni, Nj, Nk)

    print(avg_data)

    avg_img = data_to_img(avg_data, affine)

    # plot_activity_map(avg_img)
    plot_activity_map(map_to_img(avg_map, Ni, Nj, Nk, affine))


