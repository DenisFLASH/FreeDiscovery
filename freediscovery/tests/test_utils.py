#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
#from __future__ import unicode_literals

import sys
import os
import os.path
from unittest import SkipTest

import numpy as np
from numpy.testing import assert_allclose, assert_equal
import scipy.sparse
import itertools
import pytest


def test_count_duplicates():
    from freediscovery.utils import _count_duplicates

    x = np.array([1, 2, 1, 2, 2, 0])

    y = _count_duplicates(x)
    assert_equal(y, np.array([2, 3, 2, 3, 3, 1]))

