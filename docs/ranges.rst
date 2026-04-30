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

Set algebra
-----------

``VersionRange`` is closed under the standard Boolean lattice
operations.  Each combinator returns a new :class:`VersionRange`
instance and never mutates its inputs.

.. doctest::

    >>> ge1 = VersionRange.from_specifier(Specifier(">=1.0"))
    >>> lt2 = VersionRange.from_specifier(Specifier("<2.0"))
    >>> "1.5" in ge1.intersect(lt2)
    True
    >>> # Operator aliases are equivalent to the named methods.
    >>> ge1.intersect(lt2) == (ge1 & lt2)
    True
    >>> # Unions collapse touching or overlapping intervals.
    >>> a = VersionRange.exact("1.0")
    >>> b = VersionRange.exact("2.0")
    >>> "1.0" in a.union(b) and "2.0" in a.union(b)
    True
    >>> # Complement inverts the set; ``r.complement().complement() == r``.
    >>> ge1.complement().complement() == ge1
    True
    >>> # Identity factories don't require a SpecifierSet round-trip.
    >>> VersionRange.empty().is_empty
    True
    >>> "1.0" in VersionRange.unbounded()
    True
    >>> r3 = VersionRange.exact("3.0")
    >>> "3.0" in r3 and "3.1" not in r3
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
