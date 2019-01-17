import re
from functools import total_ordering

import numpy as np
from pandas.api.extensions import (
    ExtensionDtype, ExtensionArray, register_extension_dtype)
from numbers import Integral

from pandas.api.types import pandas_dtype
from pandas.core.dtypes.common import is_extension_array_dtype


def _validate_ragged_properties(start_indices, flat_array):
    """
    Validate that start_indices are flat_array arrays may be used to
    represent a valid RaggedArray.

    Parameters
    ----------
    flat_array: numpy array containing concatenation
                of all nested arrays to be represented
                by this ragged array
    start_indices: unsiged integer numpy array the same
                   length as the ragged array where values
                   represent the index into flat_array where
                   the corresponding ragged array element
                   begins
    Raises
    ------
    ValueError:
        if input arguments are invalid or incompatible properties
    """

    # Validate start_indices
    if (not isinstance(start_indices, np.ndarray) or
            start_indices.dtype.kind != 'u' or
            start_indices.ndim != 1):
        raise ValueError("""
The start_indices property of a RaggedArray must be a 1D numpy array of
unsigned integers (start_indices.dtype.kind == 'u')
    Received value of type {typ}: {v}""".format(
            typ=type(start_indices), v=repr(start_indices)))

    # Validate flat_array
    if (not isinstance(flat_array, np.ndarray) or
            flat_array.ndim != 1):
        raise ValueError("""
The flat_array property of a RaggedArray must be a 1D numpy array
    Received value of type {typ}: {v}""".format(
            typ=type(flat_array), v=repr(flat_array)))

    # Validate start_indices values
    # We don't need to check start_indices < 0 because we already know that it
    # has an unsigned integer datatype
    #
    # Note that start_indices[i] == len(flat_array) is valid as it represents
    # and empty array element at the end of the ragged array.
    invalid_inds = start_indices > len(flat_array)

    if invalid_inds.any():
        some_invalid_vals = start_indices[invalid_inds[:10]]

        raise ValueError("""
Elements of start_indices must be less than the length of flat_array ({m})
    Invalid values include: {vals}""".format(
            m=len(flat_array), vals=repr(some_invalid_vals)))


# Internal ragged element array wrapper that provides
# equality, ordering, and hashing.
@total_ordering
class _RaggedElement(object):

    @staticmethod
    def ragged_or_nan(a):
        if np.isscalar(a) and np.isnan(a):
            return a
        else:
            return _RaggedElement(a)

    @staticmethod
    def array_or_nan(a):
        if np.isscalar(a) and np.isnan(a):
            return a
        else:
            return a.array

    def __init__(self, array):
        self.array = array

    def __hash__(self):
        return hash(self.array.tobytes())

    def __eq__(self, other):
        if not isinstance(other, _RaggedElement):
            return False
        return np.array_equal(self.array, other.array)

    def __lt__(self, other):
        if not isinstance(other, _RaggedElement):
            return NotImplemented
        return _lexograph_lt(self.array, other.array)

    def __repr__(self):
        array_repr = repr(self.array)
        return array_repr.replace('array', 'ragged_element')


@register_extension_dtype
class RaggedDtype(ExtensionDtype):
    type = np.ndarray
    base = np.dtype('O')
    _subtype_re = re.compile(r"^ragged\[(?P<subtype>\w+)\]$")
    _metadata = ('_dtype',)

    @property
    def name(self):
        return 'Ragged[{subtype}]'.format(subtype=self.subtype)

    def __repr__(self):
        return self.name

    @classmethod
    def construct_array_type(cls):
        return RaggedArray

    @classmethod
    def construct_from_string(cls, string):
        # lowercase string
        string = string.lower()

        msg = "Cannot construct a 'RaggedDtype' from '{}'"
        if string.startswith('ragged'):
            # Extract subtype
            try:
                subtype_string = cls._parse_subtype(string)
                return RaggedDtype(dtype=subtype_string)
            except Exception:
                raise TypeError(msg.format(string))
        else:
            raise TypeError(msg.format(string))

    def __init__(self, dtype=np.float64):
        if isinstance(dtype, RaggedDtype):
            self._dtype = dtype.subtype
        else:
            self._dtype = np.dtype(dtype)

    @property
    def subtype(self):
        return self._dtype

    @classmethod
    def _parse_subtype(cls, dtype_string):
        """
        Parse a datatype string to get the subtype

        Parameters
        ----------
        dtype_string: str
            A string like Ragged[subtype]

        Returns
        -------
        subtype: str

        Raises
        ------
        ValueError
            When the subtype cannot be extracted
        """
        # Be case insensitive
        dtype_string = dtype_string.lower()

        match = cls._subtype_re.match(dtype_string)
        if match:
            subtype_string = match.groupdict()['subtype']
        elif dtype_string == 'ragged':
            subtype_string = 'float64'
        else:
            raise ValueError("Cannot parse {dtype_string}".format(
                dtype_string=dtype_string))
        return subtype_string


def missing(v):
    return v is None or (np.isscalar(v) and np.isnan(v))


class RaggedArray(ExtensionArray):
    def __init__(self, data, dtype=None, copy=False):
        """
        Construct a RaggedArray

        Parameters
        ----------
        data: list or array or dict or RaggedArray
            * list or 1D-array: A List or 1D array of lists or 1D arrays that
                                should be represented by the RaggedArray

            * dict: A dict containing 'start_indices' and 'flat_array' keys
                    with numpy array values where:
                    - flat_array:  numpy array containing concatenation
                                   of all nested arrays to be represented
                                   by this ragged array
                    - start_indices: unsiged integer numpy array the same
                                     length as the ragged array where values
                                     represent the index into flat_array where
                                     the corresponding ragged array element
                                     begins
            * RaggedArray: A RaggedArray instance to copy

        dtype: RaggedDtype or np.dtype or str or None (default None)
            Datatype to use to store underlying values from data.
            If none (the default) then dtype will be determined using the
            numpy.result_type function.
        copy : bool (default False)
            Whether to deep copy the input arrays. Only relevant when `data`
            has type `dict` or `RaggedArray`. When data is a `list` or
            `array`, input arrays are always copied.
        """
        if (isinstance(data, dict) and
                all(k in data for k in
                    ['start_indices', 'flat_array'])):

            _validate_ragged_properties(
                start_indices=data['start_indices'],
                flat_array=data['flat_array'])

            self._start_indices = data['start_indices']
            self._flat_array = data['flat_array']
            dtype = self._flat_array.dtype

            if copy:
                self._start_indices = self._start_indices.copy()
                self._flat_array = self._flat_array.copy()

        elif isinstance(data, RaggedArray):
            self._flat_array = data.flat_array
            self._start_indices = data.start_indices
            dtype = self._flat_array.dtype

            if copy:
                self._start_indices = self._start_indices.copy()
                self._flat_array = self._flat_array.copy()
        else:
            # Compute lengths
            index_len = len(data)
            buffer_len = sum(len(datum)
                             if not missing(datum)
                             else 0 for datum in data)

            # Compute necessary precision of start_indices array
            for nbits in [8, 16, 32, 64]:
                start_indices_dtype = 'uint' + str(nbits)
                max_supported = np.iinfo(start_indices_dtype).max
                if buffer_len <= max_supported:
                    break

            # infer dtype if not provided
            if dtype is None:
                non_missing = [np.atleast_1d(v)
                               for v in data if not missing(v)]
                if non_missing:
                    dtype = np.result_type(*non_missing)
                else:
                    dtype = 'float64'
            elif isinstance(dtype, RaggedDtype):
                dtype = dtype.subtype

            # Initialize representation arrays
            self._start_indices = np.zeros(index_len, dtype=start_indices_dtype)
            self._flat_array = np.zeros(buffer_len, dtype=dtype)

            # Populate arrays
            next_start_ind = 0
            for i, array_el in enumerate(data):
                # Compute element length
                n = len(array_el) if not missing(array_el) else 0

                # Update start indices
                self._start_indices[i] = next_start_ind

                # Update flat array
                self._flat_array[next_start_ind:next_start_ind+n] = array_el

                # increment next start index
                next_start_ind += n

        self._dtype = RaggedDtype(dtype=dtype)

    def __eq__(self, other):
        if isinstance(other, RaggedArray):
            if len(other) != len(self):
                raise ValueError("""
Cannot check equality of RaggedArray values of unequal length
    len(ra1) == {len_ra1}
    len(ra2) == {len_ra2}""".format(
                    len_ra1=len(self),
                    len_ra2=len(other)))

            result = _eq_ragged_ragged(self, other)
        else:
            # Convert other to numpy arrauy
            if not isinstance(other, np.ndarray):
                other_array = np.asarray(other)
            else:
                other_array = other

            if other_array.ndim == 1 and other_array.dtype.kind != 'O':

                # Treat as ragged scalar
                result = _eq_ragged_scalar(self, other_array)
            elif (other_array.ndim == 1 and
                  other_array.dtype.kind == 'O' and
                  len(other_array) == len(self)):

                # Treat as vector
                result = _eq_ragged_ndarray1d(self, other_array)
            elif (other_array.ndim == 2 and
                  other_array.dtype.kind != 'O' and
                  other_array.shape[0] == len(self)):

                # Treat rows as ragged elements
                result = _eq_ragged_ndarray2d(self, other_array)
            else:
                raise ValueError("""
Cannot check equality of RaggedArray of length {ra_len} with:
    {other}""".format(ra_len=len(self), other=repr(other)))

        return result

    def __ne__(self, other):
        return np.logical_not(self == other)

    @property
    def flat_array(self):
        """
        numpy array containing concatenation of all nested arrays

        Returns
        -------
        np.ndarray
        """
        return self._flat_array

    @property
    def start_indices(self):
        """
        unsiged integer numpy array the same length as the ragged array where
        values represent the index into flat_array where the corresponding
        ragged array element begins

        Returns
        -------
        np.ndarray
        """
        return self._start_indices

    def __len__(self):
        """
        Length of this array

        Returns
        -------
        length : int
        """
        return len(self._start_indices)

    def __getitem__(self, item):
        """
        Parameters
        ----------
        item : int, slice, or ndarray
            * int: The position in 'self' to get.

            * slice: A slice object, where 'start', 'stop', and 'step' are
              integers or None

            * ndarray: A 1-d boolean NumPy ndarray the same length as 'self'
        """
        if isinstance(item, Integral):
            if item < -len(self) or item >= len(self):
                raise IndexError("{item} is out of bounds".format(item=item))
            else:
                # Convert negative item index
                if item < 0:
                    item += len(self)

                slice_start = self.start_indices[item]
                slice_end = (self.start_indices[item+1]
                             if item + 1 <= len(self) - 1
                             else len(self.flat_array))

                return (self.flat_array[slice_start:slice_end]
                        if slice_end!=slice_start
                        else np.nan)

        elif type(item) == slice:
            data = []
            selected_indices = np.arange(len(self))[item]

            for selected_index in selected_indices:
                data.append(self[selected_index])

            return RaggedArray(data, dtype=self.flat_array.dtype)

        elif isinstance(item, np.ndarray) and item.dtype == 'bool':
            data = []

            for i, m in enumerate(item):
                if m:
                    data.append(self[i])

            return RaggedArray(data, dtype=self.flat_array.dtype)
        elif isinstance(item, (list, np.ndarray)):
            return self.take(item, allow_fill=False)
        else:
            raise IndexError(item)

    @classmethod
    def _from_sequence(cls, scalars, dtype=None, copy=False):
        """
        Construct a new RaggedArray from a sequence of scalars.

        Parameters
        ----------
        scalars : Sequence
            Each element will be an instance of the scalar type for this
            array, ``cls.dtype.type``.
        dtype : dtype, optional
            Construct for this particular dtype. This should be a Dtype
            compatible with the ExtensionArray.
        copy : boolean, default False
            If True, copy the underlying data.

        Returns
        -------
        RaggedArray
        """
        return RaggedArray(scalars, dtype=dtype)

    @classmethod
    def _from_factorized(cls, values, original):
        """
        Reconstruct an ExtensionArray after factorization.

        Parameters
        ----------
        values : ndarray
            An integer ndarray with the factorized values.
        original : RaggedArray
            The original ExtensionArray that factorize was called on.

        See Also
        --------
        pandas.factorize
        ExtensionArray.factorize
        """
        return RaggedArray(
            [_RaggedElement.array_or_nan(v) for v in values],
            dtype=original.flat_array.dtype)

    def _as_ragged_element_array(self):
        return np.array([_RaggedElement.ragged_or_nan(self[i])
                         for i in range(len(self))])

    def _values_for_factorize(self):
        return self._as_ragged_element_array(), np.nan

    def _values_for_argsort(self):
        return self._as_ragged_element_array()

    def unique(self):
        """
        Compute the ExtensionArray of unique values.

        Returns
        -------
        uniques : ExtensionArray
        """
        from pandas import unique

        uniques = unique(self._as_ragged_element_array())
        return self._from_sequence(
            [_RaggedElement.array_or_nan(v) for v in uniques],
            dtype=self.dtype)

    def fillna(self, value=None, method=None, limit=None):
        """
        Fill NA/NaN values using the specified method.

        Parameters
        ----------
        value : scalar, array-like
            If a scalar value is passed it is used to fill all missing values.
            Alternatively, an array-like 'value' can be given. It's expected
            that the array-like have the same length as 'self'.
        method : {'backfill', 'bfill', 'pad', 'ffill', None}, default None
            Method to use for filling holes in reindexed Series
            pad / ffill: propagate last valid observation forward to next valid
            backfill / bfill: use NEXT valid observation to fill gap
        limit : int, default None
            If method is specified, this is the maximum number of consecutive
            NaN values to forward/backward fill. In other words, if there is
            a gap with more than this number of consecutive NaNs, it will only
            be partially filled. If method is not specified, this is the
            maximum number of entries along the entire axis where NaNs will be
            filled.

        Returns
        -------
        filled : ExtensionArray with NA/NaN filled
        """
        # Override in RaggedArray to handle ndarray fill values
        from pandas.api.types import is_array_like
        from pandas.util._validators import validate_fillna_kwargs
        from pandas.core.missing import pad_1d, backfill_1d

        value, method = validate_fillna_kwargs(value, method)

        mask = self.isna()

        if isinstance(value, RaggedArray):
            if len(value) != len(self):
                raise ValueError("Length of 'value' does not match. Got ({}) "
                                 " expected {}".format(len(value), len(self)))
            value = value[mask]

        if mask.any():
            if method is not None:
                func = pad_1d if method == 'pad' else backfill_1d
                new_values = func(self.astype(object), limit=limit,
                                  mask=mask)
                new_values = self._from_sequence(new_values, dtype=self.dtype)
            else:
                # fill with value
                new_values = list(self)
                mask_indices, = np.where(mask)
                for ind in mask_indices:
                    new_values[ind] = value

                new_values = self._from_sequence(new_values, dtype=self.dtype)
        else:
            new_values = self.copy()
        return new_values

    def shift(self, periods=1, fill_value=None):
        # type: (int, object) -> ExtensionArray
        """
        Shift values by desired number.

        Newly introduced missing values are filled with
        ``self.dtype.na_value``.

        .. versionadded:: 0.24.0

        Parameters
        ----------
        periods : int, default 1
            The number of periods to shift. Negative values are allowed
            for shifting backwards.

        fill_value : object, optional
            The scalar value to use for newly introduced missing values.
            The default is ``self.dtype.na_value``

            .. versionadded:: 0.24.0

        Returns
        -------
        shifted : ExtensionArray

        Notes
        -----
        If ``self`` is empty or ``periods`` is 0, a copy of ``self`` is
        returned.

        If ``periods > len(self)``, then an array of size
        len(self) is returned, with all values filled with
        ``self.dtype.na_value``.
        """
        # Override in RaggedArray to handle ndarray fill values

        # Note: this implementation assumes that `self.dtype.na_value` can be
        # stored in an instance of your ExtensionArray with `self.dtype`.
        if not len(self) or periods == 0:
            return self.copy()

        if fill_value is None:
            fill_value = np.nan

        empty = self._from_sequence(
            [fill_value] * min(abs(periods), len(self)),
            dtype=self.dtype
        )
        if periods > 0:
            a = empty
            b = self[:-periods]
        else:
            a = self[abs(periods):]
            b = empty
        return self._concat_same_type([a, b])

    def searchsorted(self, value, side="left", sorter=None):
        """
        Find indices where elements should be inserted to maintain order.

        .. versionadded:: 0.24.0

        Find the indices into a sorted array `self` (a) such that, if the
        corresponding elements in `v` were inserted before the indices, the
        order of `self` would be preserved.

        Assuming that `a` is sorted:

        ======  ============================
        `side`  returned index `i` satisfies
        ======  ============================
        left    ``self[i-1] < v <= self[i]``
        right   ``self[i-1] <= v < self[i]``
        ======  ============================

        Parameters
        ----------
        value : array_like
            Values to insert into `self`.
        side : {'left', 'right'}, optional
            If 'left', the index of the first suitable location found is given.
            If 'right', return the last such index.  If there is no suitable
            index, return either 0 or N (where N is the length of `self`).
        sorter : 1-D array_like, optional
            Optional array of integer indices that sort array a into ascending
            order. They are typically the result of argsort.

        Returns
        -------
        indices : array of ints
            Array of insertion points with the same shape as `value`.

        See Also
        --------
        numpy.searchsorted : Similar method from NumPy.
        """
        # Note: the base tests provided by pandas only test the basics.
        # We do not test
        # 1. Values outside the range of the `data_for_sorting` fixture
        # 2. Values between the values in the `data_for_sorting` fixture
        # 3. Missing values.
        arr = self._as_ragged_element_array()
        if isinstance(value, RaggedArray):
            search_value = value._as_ragged_element_array()
        else:
            search_value = _RaggedElement(value)
        return arr.searchsorted(search_value, side=side, sorter=sorter)

    def isna(self):
        """
        A 1-D array indicating if each value is missing.

        Returns
        -------
        na_values : np.ndarray
            boolean ndarray the same length as the ragged array where values
            of True represent missing/NA values.
        """
        stop_indices = np.hstack([self.start_indices[1:],
                                  [len(self.flat_array)]])

        element_lengths = stop_indices - self.start_indices
        return element_lengths == 0

    def take(self, indices, allow_fill=False, fill_value=None):
        """
        Take elements from an array.

        Parameters
        ----------
        indices : sequence of integers
            Indices to be taken.
        allow_fill : bool, default False
            How to handle negative values in `indices`.

            * False: negative values in `indices` indicate positional indices
              from the right (the default). This is similar to
              :func:`numpy.take`.

            * True: negative values in `indices` indicate
              missing values. These values are set to `fill_value`. Any other
              other negative values raise a ``ValueError``.

        fill_value : any, default None
            Fill value to use for NA-indices when `allow_fill` is True.

        Returns
        -------
        RaggedArray

        Raises
        ------
        IndexError
            When the indices are out of bounds for the array.
        """
        if allow_fill:
            invalid_inds = [i for i in indices if i < -1]
            if invalid_inds:
                raise ValueError("""
Invalid indices for take with allow_fill True: {inds}""".format(
                    inds=invalid_inds[:9]))
            sequence = [self[i] if i >= 0 else fill_value
                        for i in indices]
        else:
            if len(self) == 0 and len(indices) > 0:
                raise IndexError("cannot do a non-empty take")

            sequence = [self[i] for i in indices]

        return RaggedArray(sequence, dtype=self.flat_array.dtype)

    def copy(self, deep=False):
        """
        Return a copy of the array.

        Parameters
        ----------
        deep : bool, default False
            Also copy the underlying data backing this array.

        Returns
        -------
        RaggedArray
        """
        data = dict(
            flat_array=self.flat_array,
            start_indices=self.start_indices)

        return RaggedArray(data, copy=deep)

    @classmethod
    def _concat_same_type(cls, to_concat):
        """
        Concatenate multiple RaggedArray instances

        Parameters
        ----------
        to_concat : list of RaggedArray

        Returns
        -------
        RaggedArray
        """
        # concat flat_arrays
        flat_array = np.hstack(ra.flat_array for ra in to_concat)

        # offset and concat start_indices
        offsets = np.hstack([
            [0],
            np.cumsum([len(ra.flat_array) for ra in to_concat[:-1]])])

        start_indices = np.hstack([ra.start_indices + offset
                                   for offset, ra in zip(offsets, to_concat)])

        return RaggedArray(dict(
            flat_array=flat_array, start_indices=start_indices),
            copy=False)

    @property
    def dtype(self):
        return self._dtype

    @property
    def nbytes(self):
        """
        The number of bytes needed to store this object in memory.
        """
        return (self._flat_array.nbytes +
                self._start_indices.nbytes)

    def astype(self, dtype, copy=True):

        dtype = pandas_dtype(dtype)
        if isinstance(dtype, RaggedDtype):
            if copy:
                return self.copy()
            return self

        elif is_extension_array_dtype(dtype):
            return dtype.construct_array_type()._from_sequence(
                np.asarray(self))

        return np.array([v for v in self], dtype=dtype, copy=copy)


def _eq_ragged_ragged(ra1, ra2):
    """
    Compare elements of two ragged arrays of the same length

    Parameters
    ----------
    ra1: RaggedArray
    ra2: RaggedArray

    Returns
    -------
    mask: ndarray
        1D bool array of same length as inputs with elements True when
        corresponding elements are equal, False otherwise
    """
    start_indices1 = ra1.start_indices
    flat_array1 = ra1.flat_array

    start_indices2 = ra2.start_indices
    flat_array2 = ra2.flat_array

    n = len(start_indices1)
    m1 = len(flat_array1)
    m2 = len(flat_array2)

    result = np.zeros(n, dtype=np.bool)

    for i in range(n):
        # Extract inds for ra1
        start_index1 = start_indices1[i]
        stop_index1 = start_indices1[i + 1] if i < n - 1 else m1

        # Extract inds for ra2
        start_index2 = start_indices2[i]
        stop_index2 = start_indices2[i + 1] if i < n - 1 else m2

        result[i] = np.array_equal(flat_array1[start_index1:stop_index1],
                                   flat_array2[start_index2:stop_index2])

    return result


def _eq_ragged_scalar(ra, val):
    """
    Compare elements of a RaggedArray with a scalar array

    Parameters
    ----------
    ra: RaggedArray
    val: ndarray

    Returns
    -------
    mask: ndarray
        1D bool array of same length as inputs with elements True when
        ragged element equals scalar val, False otherwise.
    """
    start_indices = ra.start_indices
    flat_array = ra.flat_array

    n = len(start_indices)
    m = len(flat_array)
    result = np.zeros(n, dtype=np.bool)
    for i in range(n):
        start_index = start_indices[i]
        stop_index = start_indices[i+1] if i < n - 1 else m
        result[i] = np.array_equal(flat_array[start_index:stop_index], val)

    return result


def _eq_ragged_ndarray1d(ra, a):
    """
    Compare a RaggedArray with a 1D numpy object array of the same length

    Parameters
    ----------
    ra: RaggedArray
    a: ndarray
        1D numpy array of same length as ra

    Returns
    -------
    mask: ndarray
        1D bool array of same length as input with elements True when
        corresponding elements are equal, False otherwise
    """
    start_indices = ra.start_indices
    flat_array = ra.flat_array

    n = len(start_indices)
    m = len(flat_array)
    result = np.zeros(n, dtype=np.bool)
    for i in range(n):
        start_index = start_indices[i]
        stop_index = start_indices[i + 1] if i < n - 1 else m
        a_val = a[i]
        if (a_val is None or
                (np.isscalar(a_val) and np.isnan(a_val)) or
                len(a_val) == 0):
            result[i] = start_index == stop_index
        else:
            result[i] = np.array_equal(flat_array[start_index:stop_index],
                                       a_val)

    return result


def _eq_ragged_ndarray2d(ra, a):
    """
    Compare a RaggedArray with rows of a 2D numpy object array

    Parameters
    ----------
    ra: RaggedArray
    a: ndarray
        A 2D numpy array where the length of the first dimension matches the
        length of the RaggedArray

    Returns
    -------
    mask: ndarray
        1D bool array of same length as input RaggedArray with elements True
        when corresponding elements of ra equals corresponding row of a
    """
    start_indices = ra.start_indices
    flat_array = ra.flat_array

    n = len(start_indices)
    m = len(flat_array)
    result = np.zeros(n, dtype=np.bool)
    for i in range(n):
        start_index = start_indices[i]
        stop_index = start_indices[i + 1] if i < n - 1 else m
        result[i] = np.array_equal(flat_array[start_index:stop_index],
                                   a[i, :])
    return result


def _lexograph_lt(a1, a2):
    """
    Compare two 1D numpy arrays lexographically
    Parameters
    ----------
    a1: ndarray
        1D numpy array
    a2: ndarray
        1D numpy array

    Returns
    -------
    comparison:
        True if a1 < a2, False otherwise
    """
    for e1, e2 in zip(a1, a2):
        if e1 < e2:
            return True
        elif e1 > e2:
            return False
    return len(a1) < len(a2)
