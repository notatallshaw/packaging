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
    >>> # Specifiers with the ``===`` arbitrary-equality operator
    >>> # produce a carve-out range with the literal in ``_arbitrary``.
    >>> arb = VersionRange.from_specifier_set(SpecifierSet("===wat"))
    >>> arb._arbitrary
    'wat'
    >>> "wat" in arb and "WAT" in arb
    True
    >>> # ``VersionRange.full()`` admits arbitrary strings, mirroring
    >>> # ``SpecifierSet("")``; other ranges reject unparseable inputs.
    >>> "not-a-version" in VersionRange.full()
    True
    >>> "not-a-version" in VersionRange.from_specifier(Specifier(">=1.0"))
    False

Set algebra
-----------

``VersionRange`` is closed under the standard Boolean lattice
operations.  Each combinator returns a new :class:`VersionRange`
instance and never mutates its inputs.  The ``===`` carve-out is the
exception: arbitrary-equality ranges are not members of the lattice
and :meth:`~VersionRange.intersection`, :meth:`~VersionRange.union`,
and :meth:`~VersionRange.complement` raise :exc:`TypeError` when
either operand carries one.

.. doctest::

    >>> ge1 = VersionRange.from_specifier(Specifier(">=1.0"))
    >>> lt2 = VersionRange.from_specifier(Specifier("<2.0"))
    >>> "1.5" in ge1.intersection(lt2)
    True
    >>> # Operator aliases are equivalent to the named methods.
    >>> ge1.intersection(lt2) == (ge1 & lt2)
    True
    >>> # Unions collapse touching or overlapping intervals.
    >>> a = VersionRange.singleton("1.0")
    >>> b = VersionRange.singleton("2.0")
    >>> "1.0" in a.union(b) and "2.0" in a.union(b)
    True
    >>> # Complement inverts the set; ``r.complement().complement() == r``.
    >>> ge1.complement().complement() == ge1
    True
    >>> # Identity factories don't require a SpecifierSet round-trip.
    >>> VersionRange.empty().is_empty
    True
    >>> "1.0" in VersionRange.full()
    True
    >>> r3 = VersionRange.singleton("3.0")
    >>> "3.0" in r3 and "3.1" not in r3
    True

Converting back to a SpecifierSet
---------------------------------

:class:`SpecifierSet` is closed under intersection (just concatenate
specifiers with commas) but **not** under :meth:`union` or
:meth:`complement`: PEP 440 has no specifier for the strict singleton
``{V}`` (``==V`` is wider since it also matches ``V+local``), nor for
the inclusive ``AFTER_POSTS`` upper bound that arises from
complementing ``>V``.  The conversion methods are therefore partial.

* :meth:`VersionRange.to_specifier_set` returns a single ``SpecifierSet``
  whose ``from_specifier_set`` round-trips to *self*, or ``None`` if no
  such single set exists.  Walks consecutive interval pairs detecting
  ``!=V`` and ``!=V.*`` exclusion gaps; outer bounds plus the chain of
  ``!=`` fragments form a single ``SpecifierSet``.
* :meth:`VersionRange.to_specifier_sets` returns a tuple of
  ``SpecifierSet``\ s whose union equals *self*, or ``None`` if any
  interval has a bound that no specifier can express.  Strictly more
  permissive than the single-set form; for unions of disjoint specifier-
  shaped intervals each interval encodes separately.

The empty range round-trips through ``SpecifierSet("<0")``: ``<0``
parses with upper ``0.dev0`` (excl) and ``0.dev0`` is the smallest
possible PEP 440 version, so the resulting range contains nothing.

.. doctest::

    >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0,!=1.5"))
    >>> str(r.to_specifier_set())
    '!=1.5,<2.0,>=1.0'
    >>> # Strict singletons (no local segment) have no specifier form.
    >>> VersionRange.singleton("1.5").to_specifier_set() is None
    True
    >>> # Disjoint unions encode per-interval.
    >>> a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
    >>> b = VersionRange.from_specifier_set(SpecifierSet(">=3.0,<4.0"))
    >>> [str(s) for s in (a | b).to_specifier_sets()]
    ['<2.0,>=1.0', '<4.0,>=3.0']
    >>> VersionRange.empty().to_specifier_set() == SpecifierSet("<0")
    True
    >>> # ``===`` carve-out ranges round-trip through ``===<literal>``
    >>> # (combined with rangelike fragments when present).
    >>> arb = VersionRange.from_specifier_set(SpecifierSet("===wat"))
    >>> str(arb.to_specifier_set())
    '===wat'


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
