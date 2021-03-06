# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os.path
import re
import shutil
import numpy as np

from sklearn.externals import joblib
from sklearn.externals.joblib import Parallel, delayed
from sklearn.feature_extraction.text import TfidfTransformer, TfidfVectorizer
from sklearn.preprocessing import normalize

from .base import _BaseTextTransformer
from .utils import generate_uuid, _rename_main_thread
from .exceptions import (DatasetNotFound, InitException, NotFound, WrongParameter)


def _touch(filename):
    open(filename, 'ab').close()


def _vectorize_chunk(dsid_dir, k, pars, pretend=False):
    """ Extract features on a chunk of files """
    import os.path
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.externals import joblib

    filenames = pars['filenames_abs']
    chunk_size = pars['chunk_size']
    n_samples = pars['n_samples']

    mslice = slice(k*chunk_size, min((k+1)*chunk_size, n_samples))

    if pars['use_idf']:
        pars['binary'] = False # need to apply TFIDF weights first 

    hash_opts = {key: vals for key, vals in pars.items() \
            if key in ['stop_words', 'n_features', 'binary', 'analyser', 'ngram_range']}
    fe = HashingVectorizer(input='filename', norm=None, decode_error='ignore',
           non_negative=True, **hash_opts) 
    if pretend:
        return
    fset_new = fe.transform(filenames[mslice])

    fset_new.eliminate_zeros()

    joblib.dump(fset_new, os.path.join(dsid_dir, 'features-{:05}'.format(k)),
            compress=0)


class FeatureVectorizer(_BaseTextTransformer):
    """Extract features from text documents

    Parameters
    ----------
    cache_dir : str, default='/tmp/'
        directory where to save temporary and regression files
    dsid : str
        load an exising dataset
    verbose : bool
        pring progress messages
    """

    _PARS_SHORT = ['data_dir', 'n_samples', 'n_features',
                   'n_jobs', 'chunk_size', 'norm',
                   'analyzer', 'ngram_range', 'stop_words',
                   'use_idf', 'sublinear_tf', 'binary', 'use_hashing']

    def preprocess(self, data_dir, file_pattern='.*', dir_pattern='.*',  n_features=11000000,
            chunk_size=5000, analyzer='word', ngram_range=(1, 1), stop_words='None',
            n_jobs=1, use_idf=False, sublinear_tf=False, binary=True, use_hashing=True,
            norm=None, min_df=0.0, max_df=1.0):
        """Initalize the features extraction. See sklearn.feature_extraction.text for a
        detailed description of the input parameters """
        data_dir = os.path.normpath(data_dir)

        if not os.path.exists(data_dir):
            raise NotFound('data_dir={} does not exist'.format(data_dir))
        self.data_dir = data_dir

        filenames = self._list_filenames(data_dir, dir_pattern, file_pattern)

        if not filenames: # no files were found
            raise WrongParameter('No files to process were found!')
        if analyzer not in ['word', 'char', 'char_wb']:
            raise WrongParameter('analyzer={} not supported!'.format(analyzer))
        if not isinstance(ngram_range, tuple) and not isinstance(ngram_range, list):
            raise WrongParameter('not a valid input ngram_range={}: should be a list or a typle!'.format(ngram_range))
        if not len(ngram_range) == 2:
            raise WrongParameter('len(gram_range=={}!=2'.format(len(ngram_range)))
        if stop_words not in ['None', 'english', 'english_alphanumeric']:
            raise WrongParameter('stop_words')

        filenames_rel = [os.path.relpath(el, data_dir) for el in filenames]
        self.dsid = dsid = generate_uuid()
        self.dsid_dir = dsid_dir = os.path.join(self.cache_dir, dsid)

        # hash collision, should not happen
        if os.path.exists(dsid_dir):
            shutil.rmtree(dsid_dir)

        os.mkdir(dsid_dir)
        pars = {'filenames': filenames_rel, 'data_dir': data_dir,
                'n_samples': len(filenames_rel), "n_features": n_features,
                'chunk_size': chunk_size, 'stop_words': stop_words,
                'analyzer': analyzer, 'ngram_range': ngram_range,
                'n_jobs': n_jobs, 'use_idf': use_idf, 'sublinear_tf': sublinear_tf,
                'binary': binary, 'use_hashing': use_hashing,
                'norm': norm, 'min_df': min_df, 'max_df': max_df,
                'type': type(self).__name__
               }
        self._pars = pars
        joblib.dump(pars, os.path.join(dsid_dir, 'pars'), compress=9)
        return dsid

    @staticmethod
    def _generate_stop_words(stop_words):
        from sklearn.feature_extraction.stop_words import ENGLISH_STOP_WORDS
        import string
        from itertools import product
        if stop_words in ['None']:
            return None
        elif stop_words == 'english':
            return stop_words
        #elif stop_words == 'english_alphanumeric':
        #    stop_words_list = list(ENGLISH_STOP_WORDS)
        #    stop_words_list += [''.join(i) for i in product(
        #                                string.ascii_lowercase + string.digits, repeat=2)]
        #    return stop_words_list
        else:
            raise ValueError

    def transform(self):
        """
        Run the feature extraction
        """
        from glob import glob

        dsid = self.dsid
        dsid_dir = self.dsid_dir
        if not os.path.exists(dsid_dir):
            raise DatasetNotFound()

        pars = self._pars
        filenames_base = pars['filenames']
        data_dir = pars['data_dir']
        pars['filenames_abs'] = [os.path.join(data_dir, el) for el in filenames_base]
        chunk_size = pars['chunk_size']
        n_samples = pars['n_samples']
        n_jobs = pars['n_jobs']
        use_hashing = pars['use_hashing']

        if use_hashing:
            # just make sure that we can initialize the vectorizer
            # (easier outside of the paralel loop
            _vectorize_chunk(dsid_dir, 0, pars, pretend=True)

        processing_lock =  os.path.join(dsid_dir, 'processing')
        _touch(processing_lock)
        pars['stop_words'] = self._generate_stop_words(pars['stop_words'])

        try:
            if use_hashing:
                _rename_main_thread() # fixed in https://github.com/joblib/joblib/pull/414
                Parallel(n_jobs=n_jobs)(delayed(_vectorize_chunk)(dsid_dir, k, pars)\
                            for k in range(n_samples//chunk_size + 1))

                res = self._aggregate_features()

                if pars['use_idf']:
                    tfidf = TfidfTransformer(norm=pars['norm'], use_idf=True,
                                              sublinear_tf=pars['sublinear_tf'])
                    res = tfidf.fit_transform(res)
                self.vect = None
            else:
                opts_tfidf = {key: val for key, val in pars.items() \
                        if key in ['stop_words', 'use_idf', 'ngram_range', 'analyzer',
                                   'min_df', 'max_df']}

                tfidf = TfidfVectorizer(input='filename',
                            max_features=pars['n_features'],
                            norm=pars['norm'],
                            decode_error='ignore', **opts_tfidf)
                res = tfidf.fit_transform(pars['filenames_abs'])
                joblib.dump(tfidf, os.path.join(dsid_dir, 'vectorizer'))
                self.vect = tfidf

            if pars['norm'] is not None:
                res = normalize(res, norm=pars['norm'], copy=False)
            else:
                # scale feature to [0, 1]
                # this is necessary e.g. by SVM
                # and does not hurt anyway
                res /= res.max()

            joblib.dump(res, os.path.join(dsid_dir, 'features'))
            # remove all identical files
            if use_hashing:
                for filename in glob(os.path.join(dsid_dir, 'features-*[0-9]*')):
                    os.remove(filename)
        except:
            if os.path.exists(processing_lock):
                os.remove(processing_lock)
            raise
        # remove processing lock if finished or if error
        if os.path.exists(processing_lock):
            os.remove(processing_lock)
        _touch(os.path.join(dsid_dir, 'processing_finished'))
        return dsid, filenames_base

    def query_features(self, indices, n_top_words=10):
        """ Query the features with most weight"""

        # this should raise a warning when used with wrong weights
        X = joblib.load(os.path.join(self.dsid_dir, 'features'))
        X = X[indices]

        centroid = X.sum(axis=0).view(type=np.ndarray)[0] / len(indices)
        order_centroid = centroid.argsort()[::-1]
        terms = self.vect.get_feature_names()

        out = []
        for ridx, idx in enumerate(order_centroid):
            if ridx >= n_top_words:
                break
            out.append(terms[idx])
        return out


    def _aggregate_features(self):
        """ Agregate features loaded as separate files features-<number>
        into a single file features"""
        from glob import glob
        import scipy.sparse
        out = []
        for filename in sorted(glob(os.path.join(self.dsid_dir, 'features-*[0-9]'))):
            ds = joblib.load(filename)
            out.append(ds)
        res = scipy.sparse.vstack(out)
        return res
