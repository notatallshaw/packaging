Ranges
======

.. versionadded:: 26.3

A :class:`~packaging.ranges.VersionRange` is a set of
:class:`~packaging.version.Version` values, expressed as a union of
zero or more disjoint intervals on the PEP 440 version ordering.

Instances are not constructed directly. They are produced by
:meth:`packaging.specifiers.VersionRange.from_specifier` and
:meth:`packaging.specifiers.VersionRange.from_specifier_set`. The class
itself is importable so that it can be used in type annotations and
:func:`isinstance` checks.

Usage
-----

.. doctest::

    >>> from packaging.ranges import VersionRange
    >>> from packaging.specifiers import Specifier, SpecifierSet
    >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
    >>> isinstance(r, VersionRange)
    True
    >>> "1.5" in r
    True
    >>> "2.0" in r
    False
    >>> # Unsatisfiable specifier sets produce an empty range, not None.
    >>> empty = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))
    >>> empty.is_empty
    True
    >>> bool(empty)
    False
    >>> # Specifiers with the ``===`` arbitrary-equality operator cannot be
    >>> # expressed as a version range; the factories return ``None``.
    >>> VersionRange.from_specifier_set(SpecifierSet("===wat")) is None
    True


.. note::

    Calling :class:`~packaging.ranges.VersionRange` directly raises
    :exc:`TypeError`. Always go through
    :meth:`~packaging.ranges.VersionRange.from_specifier` or
    :meth:`~packaging.ranges.VersionRange.from_specifier_set`.

    >>> VersionRange()  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
        ...
    TypeError: cannot create 'VersionRange' instances directly; ...


Reference
---------

.. autoclass:: packaging.ranges.VersionRange
    :members:
    :special-members: __contains__, __bool__, __eq__, __hash__, __repr__
