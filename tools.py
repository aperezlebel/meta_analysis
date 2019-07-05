import scipy
import numpy as np

from globals import mem, Ni, Nj, Nk

def print_percent(index, total, prefix='', rate=10000):
    if (total//rate) == 0 or index % (total//rate) == 0:
        print(prefix+str(round(100*index/total, 1))+'%')

@mem.cache
def build_index(file_path):
    '''
        Build decode & encode dictionnary of the given file_name.

        encode : dict
            key : line number
            value : string at the specified line number
        decode : dict (reverse of encode)
            key : string found in the file
            value : number of the line containing the string

        Used for the files pmids.txt & feature_names.txt
    '''
    decode = dict(enumerate(line.strip() for line in open(file_path)))
    encode = {v: k for k, v in decode.items()}
    
    return encode, decode

def empirical_cov_matrix(observations):
    n_observations = observations.shape[0]

    s_X = scipy.sparse.csr_matrix(observations)
    s_Ones = scipy.sparse.csr_matrix(np.ones(n_observations))

    M1 = s_X.transpose().dot(s_X)
    M2 = (s_Ones.dot(s_X)).transpose()
    M3 = s_Ones.dot(s_X)

    return M1/n_observations - M2.dot(M3)/(n_observations**2)

def index_3D_to_1D(i, j, k, Ni, Nj, Nk):
    return i + Ni*j + Ni*Nj*k

def index_1D_to_3D(p, Ni, Nj, Nk):
    k = p//(Ni*Nj)
    p = p%(Ni*Nj)
    j = p//Ni
    i = p%Ni
    return i, j, k

def index_3D_to_1D_checked(i, j, k, Ni, Nj, Nk):
    if i >= Ni or j >= Nj or k >= Nk or i < 0 or j < 0 or k < 0:
        raise ValueError('Indices ({}, {}, {}) are outside box of size ({}, {}, {}).'.format(i, j, k, Ni, Nj, Nk))

    if Ni == 0 or Nj == 0 or Nk == 0:
        raise ValueError('Given box of size ({}, {}, {}) should not have a null side.'.format(Ni, Nj, Nk))

    return index_3D_to_1D(i, j, k, Ni, Nj, Nk)

def index_1D_to_3D_checked(p, Ni, Nj, Nk):
    size = Ni*Nj*Nk
    if p >= size or p < 0:
        raise ValueError('Indice {} is outside vector of size {}*{}*{}={}.'.format(p, Ni, Nj, Nk, size))

    if Ni == 0 or Nj == 0 or Nk == 0:
        raise ValueError('Given box of size ({}, {}, {}) should not have a null side.'.format(Ni, Nj, Nk))

    return index_1D_to_3D(p, Ni, Nj, Nk)

def map_to_data(map, Ni, Nj, Nk):
    '''
        Convert a sparse CSR matrix of shape (n_voxels, 1) into a dense 3D numpy array of shape (Ni, Nj, Nk).

        Indexing of map is supposed to have been made Fortran like.
    '''
    n_voxels, _ = map.shape

    if n_voxels != Ni*Nj*Nk:
        raise ValueError('Map\'s length ({}) does not match given box ({}, {}, {}) of size {}'.format(n_voxels, Ni, Nj, Nk, Ni*Nj*Nk))

    # data = np.zeros((Ni, Nj, Nk))

    # non_zeros, _ = map.nonzero()

    # for p in non_zeros:
    #     # print(p)
    #     i, j, k = index_1D_to_3D(p, Ni, Nj, Nk)
    #     data[i, j, k] = map[p, 0]

    # data = map.toarray()

    # data = data.reshape((Ni, Nj, Nk))


    return map.toarray().reshape((Ni, Nj, Nk), order='F')

if __name__ == '__main__':
    i, j, k = 3, 4, 5

    p = index_3D_to_1D(i, j, k, Ni, Nj, Nk)
    print(p)
    print(index_1D_to_3D(p, Ni, Nj, Nk))

    p = 34789
    i, j, k = index_1D_to_3D(p, Ni, Nj, Nk)
    print(i, j, k)
    print(index_3D_to_1D(i, j, k, Ni, Nj, Nk))