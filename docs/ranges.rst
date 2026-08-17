Version Ranges
==============

.. currentmodule:: packaging.ranges

A :class:`VersionRange` is the set of :class:`~packaging.version.Version`
values accepted by a :class:`~packaging.specifiers.SpecifierSet`, viewed as
intervals on the PEP 440 ordering. It supports intersection, union,
complement, and difference, so tooling that combines many requirements, such
as a resolver, can work on the intervals directly.

.. versionadded:: 26.3

Usage
-----

.. doctest::

    >>> from packaging.ranges import VersionRange
    >>> from packaging.specifiers import SpecifierSet
    >>> from packaging.version import Version
    >>> # Build a range from a specifier set
    >>> r = SpecifierSet(">=1.0,<2.0").to_range()
    >>> r
    <VersionRange '[1.0, 2.0.dev0)'>
    >>> Version("1.5") in r
    True
    >>> Version("2.0") in r
    False
    >>> # Combine ranges with set algebra
    >>> a = SpecifierSet(">=1.0").to_range()
    >>> b = SpecifierSet("<2.0").to_range()
    >>> a & b == r
    True
    >>> # The union covers either side, leaving any gap between them
    >>> u = SpecifierSet("<1.0").to_range() | SpecifierSet(">=2.0").to_range()
    >>> Version("0.5") in u
    True
    >>> Version("1.5") in u
    False
    >>> # The complement is every other version
    >>> Version("0.5") in ~r
    True
    >>> # Filter an iterable of versions
    >>> list(r.filter(["0.9", "1.5", "2.0"]))
    ['1.5']
    >>> # An unsatisfiable set produces the empty range
    >>> SpecifierSet(">=2.0,<1.0").to_range().is_empty
    True
    >>> # Convert back to a SpecifierSet when possible
    >>> str(r.to_specifier_set())
    '<2.0,>=1.0'

Building from raw bounds
------------------------

:meth:`VersionRange.from_bounds` builds the interval between two order cuts
directly, without going through a specifier set. It is the constructor to reach
for when the boundaries are already known, and it places cuts PEP 440 syntax
cannot spell, such as an upper bound admitting ``2.0`` but not ``2.0+local``.

.. doctest::

    >>> window = VersionRange.from_bounds("1.0", "2.0")
    >>> Version("2.0") in window
    True
    >>> Version("2.0+local") in window
    False
    >>> Version("2.0") in VersionRange.from_bounds("1.0", "2.0", include_upper=False)
    False

Membership is decided by the cuts alone, so it differs from the specifier set
that reads the same way. ``<2.0`` excludes the pre-releases of its own bound,
so it excludes ``2.0rc1``, and ``>1.0`` excludes the post-releases of its own
bound, so it excludes ``1.0.post1``; raw bounds do neither:

.. doctest::

    >>> Version("2.0rc1") in VersionRange.from_bounds("1.0", "2.0")
    True
    >>> Version("2.0rc1") in SpecifierSet(">=1.0,<2.0").to_range()
    False

A range built from raw cuts usually has no PEP 440 spelling, and
:meth:`VersionRange.to_specifier_set` returns ``None`` when it has none:

.. doctest::

    >>> VersionRange.from_bounds("1.0", "2.0").to_specifier_set() is None
    True

.. versionadded:: 26.4

Pre-releases
------------

A specifier that names a pre-release, such as ``>=2.0b1``, opts in pre-releases
only for the versions it asks for. That opt-in region is carried as ranges are
combined, so a union with a plain range keeps the pre-releases it named without
admitting every pre-release below them:

.. doctest::

    >>> a = SpecifierSet(">=1.0").to_range()
    >>> b = SpecifierSet(">=2.0b1").to_range()
    >>> list((a | b).filter(["1.5b1", "2.0b1", "2.5"]))
    ['2.0b1', '2.5']

``2.0b1`` is admitted because ``>=2.0b1`` asked for it; ``1.5b1`` is not, since
the opt-in never came from ``>=1.0``.

The opt-in is also clipped to the range's own bounds, so combining ranges never
force-admits a pre-release outside every operand's request. ``>=2.0b1,<3`` opts
pre-releases in only below ``3``, so unioning it with an unrelated higher range
does not admit a pre-release up in that range:

.. doctest::

    >>> capped = SpecifierSet(">=2.0b1,<3").to_range()
    >>> other = SpecifierSet(">=3.5,<4").to_range()
    >>> list((capped | other).filter(["3.6b1", "3.7"]))
    ['3.7']

Neither operand admits ``3.6b1``: ``>=3.5,<4`` names no pre-release and
``>=2.0b1,<3`` opts in only below ``3``.

Set difference
--------------

``a - b`` is set difference: the versions in ``a`` but not ``b``. It agrees with
``a & ~b`` on the version set and the opt-in region, so subtracting a
pre-release-naming range does not leak its pre-releases into the result:

.. doctest::

    >>> base = SpecifierSet(">=1.0").to_range()
    >>> excluded = SpecifierSet(">=2.0b1").to_range()
    >>> list((base - excluded).filter(["1.9", "2.0a1"]))
    ['1.9']
    >>> (base - excluded) == (base & ~excluded)
    True

Comparing ranges
----------------

Equality on a :class:`VersionRange` is structural: it compares the bounds, the
``===`` admit/reject literals, the arbitrary-string flag, the configured
pre-release policy, and the opt-in region, not only the version set. Equal
ranges therefore behave identically under :meth:`VersionRange.contains` and
:meth:`VersionRange.filter`. The guarantee is one-directional: ``==1.0`` and
``>=1.0,<1.0.post0.dev0`` compare unequal (the second carries an opt-in
region), yet no pre-release exists in that window, so they filter
identically.

For set relations use :meth:`VersionRange.is_subset`,
:meth:`VersionRange.is_superset`, and :meth:`VersionRange.is_disjoint` rather
than comparing intersections by hand. Intersection can change the opt-in
region without changing the version set, so the textbook subset test
``a & b == a`` can report a false negative. Each method compares the version
sets directly, so it is not affected by that opt-in difference:

.. doctest::

    >>> from packaging.specifiers import SpecifierSet
    >>> a = SpecifierSet(">=1.0").to_range()
    >>> b = SpecifierSet(">=1.0a1").to_range()
    >>> # Every version >=1.0 is also >=1.0a1, so a is a subset of b. But b
    >>> # opts pre-releases in, so ``a & b`` and ``a`` differ in the opt-in region:
    >>> a & b == a
    False
    >>> a.is_subset(b)
    True
    >>> b.is_superset(a)
    True
    >>> a.is_disjoint(b)
    False
    >>> # The opt-in difference is observable: a & b force-admits pre-releases
    >>> # in b's region that plain a filters out
    >>> list(a.filter(["2.0a1", "2.5"]))
    ['2.5']
    >>> list((a & b).filter(["2.0a1", "2.5"]))
    ['2.0a1', '2.5']

Like :meth:`VersionRange.intersection`, :meth:`VersionRange.union`, and
:meth:`VersionRange.difference`, these predicates require both operands to share
the same configured pre-release policy and raise :exc:`ValueError` otherwise.

Because the operations refuse to mix policies, a range's version set is
well-defined only under its own configured policy, and set relations stay sound
only while that policy is held fixed. Reinterpreting a range, or a
:class:`~packaging.specifiers.SpecifierSet` recovered from one, under a
different ``prereleases`` value is therefore unsound: under ``prereleases=False``
the ranges of ``<=1.0,!=1.0`` and ``<1.0`` accept the same releases (they differ
only in ``1.0``'s pre-releases, which the policy excludes), but that equivalence
is gone once the policy changes.

Different specifiers that denote the same range, opt-in region included,
canonicalize to one form, so they compare equal. ``>1.0a1`` excludes
``1.0a1``'s post-releases per PEP 440, so its smallest member is ``1.0a2.dev0``,
exactly the set of ``>=1.0a2.dev0``:

.. doctest::

    >>> r1 = SpecifierSet(">1.0a1").to_range()
    >>> r2 = SpecifierSet(">=1.0a2.dev0").to_range()
    >>> r1 == r2
    True
    >>> third = SpecifierSet("<2.0").to_range()
    >>> (r1 & third) == (r2 & third)
    True

The opt-in region is also part of equality. ``<1.0.post0.dev0`` and
``<=1.0`` cover the same versions, but the first autodetects an opt-in
region from its ``.dev`` bound, so it admits pre-releases by default,
while the second does not; they are not substitutable and compare unequal:

.. doctest::

    >>> SpecifierSet("<1.0.post0.dev0").to_range() == SpecifierSet("<=1.0").to_range()
    False

Snapping onto released versions
-------------------------------

Set algebra leaves bounds at versions nobody released. Removing
``>=1.1,<1.2`` from ``>=1.0,<2.0`` cuts the range at ``1.1`` and ``1.2.dev0``,
though the versions actually published are ``1.0``, ``1.5``, ``1.9`` and
``2.5``:

.. doctest::

    >>> published = ["1.0", "1.5", "1.9", "2.5"]
    >>> source = SpecifierSet(">=1.0,<2.0").to_range()
    >>> gapped = source - SpecifierSet(">=1.1,<1.2").to_range()
    >>> gapped
    <VersionRange '[1.0, 1.1) | [1.2.dev0, 2.0.dev0)'>

:meth:`VersionRange.snap_bounds` moves each finite bound inward onto the
outermost given version its segment contains, which restates the range in terms
of versions that exist:

.. doctest::

    >>> gapped.snap_bounds(published)
    <VersionRange '[1.0, 1.0] | [1.5, 1.9]'>

The result is always a subset of the original, so a stale or incomplete list
costs precision and never soundness: snapping can drop versions, and cannot
admit one the original excluded. It also agrees with the original on every
version given to it, and snapping again on the same list changes nothing.

.. doctest::

    >>> snapped = gapped.snap_bounds(published)
    >>> snapped.is_subset(gapped)
    True
    >>> [v for v in published if snapped.contains(v)] == [
    ...     v for v in published if gapped.contains(v)
    ... ]
    True
    >>> snapped.snap_bounds(published) == snapped
    True

An unbounded end stays unbounded, and a segment holding none of the given
versions is left alone.

.. versionadded:: 26.4

Projecting onto a release grid
------------------------------

Some decisions are keyed on a release number rather than on a full version, an
interpreter version being the common case. :meth:`VersionRange.release_intervals`
projects a range onto the releases written with a fixed number of numeric
components and returns the runs of them the range contains, as half-open
``[lower, upper)`` pairs with ``None`` for an unbounded side:

.. doctest::

    >>> SpecifierSet(">=3.11.4").to_range().release_intervals(3)
    ((<Version('3.11.4')>, None),)
    >>> SpecifierSet("!=3.11.4").to_range().release_intervals(3)
    ((None, <Version('3.11.4')>), (<Version('3.11.5')>, None))

The edges are where the range stops admitting releases, so a caller can split
on them instead of testing every release. Only the grid points are reported:
a range that holds finer versions but no release of that shape reports nothing,
and pre-releases, post-releases and locals between two releases are invisible.

.. doctest::

    >>> SpecifierSet(">=3.10.2").to_range().release_intervals(2)
    ((<Version('3.11')>, None),)
    >>> SpecifierSet("~=3.10.2").to_range().release_intervals(2)
    ()

``~=3.10.2`` admits everything from ``3.10.2`` up to ``3.11``, and no
two-component release sits in there, so on that grid it covers nothing.

A release falls in one of the runs exactly when :meth:`VersionRange.contains`
accepts it, ``===`` literals included. A literal matches as a string, so it
reaches the grid only where it is spelled the way one of these releases is:

.. doctest::

    >>> split = SpecifierSet(">=2.0,<4.0").to_range()
    >>> split -= SpecifierSet("===3.0").to_range()
    >>> split.release_intervals(2)
    ((<Version('2.0')>, <Version('3.0')>), (<Version('3.1')>, <Version('4.0')>))
    >>> split.release_intervals(1)
    ((<Version('2')>, <Version('4')>),)

``===3.0`` removes the two-component release ``3.0``, splitting the run. On the
one-component grid it removes nothing, because ``3`` and ``3.0`` are different
strings and the literal only matches the second.

.. versionadded:: 26.4

Recovering a specifier set
--------------------------

:meth:`VersionRange.to_specifier_set` converts a range back to a
:class:`~packaging.specifiers.SpecifierSet`. Specifier syntax cannot express
every range, so it returns the simplest set whose own
:meth:`~packaging.specifiers.SpecifierSet.to_range` reproduces the range, or
``None`` when no single set does:

.. doctest::

    >>> str(SpecifierSet(">=1.0,<2.0").to_range().to_specifier_set())
    '<2.0,>=1.0'
    >>> # A disjoint union usually has no single-set spelling
    >>> a = SpecifierSet("<1").to_range()
    >>> b = SpecifierSet(">=2").to_range()
    >>> (a | b).to_specifier_set() is None
    True
    >>> # unless the gap between the pieces is expressible as exclusions
    >>> u = SpecifierSet("==1.*").to_range() | SpecifierSet("==3.*").to_range()
    >>> str(u.to_specifier_set())
    '!=0.*,!=2.*,<4'
    >>> # The strict singleton has no spelling: ==1.5 would also match 1.5+local
    >>> VersionRange.singleton("1.5").to_specifier_set() is None
    True
    >>> # Neither does the full range built by algebra: it opts no pre-release
    >>> # in, while its only spelling, >=0.dev0, opts them all in
    >>> r = SpecifierSet(">=2").to_range()
    >>> (r | ~r).to_specifier_set() is None
    True
    >>> # full() itself admits arbitrary strings, which the empty set spells
    >>> str(VersionRange.full().to_specifier_set())
    ''

The recovery also caps how many ``!=`` exclusions it will write out, returning
``None`` past the cap; without it, some ranges would convert to pathologically
large sets that are slow to build and to use:

.. doctest::

    >>> far = SpecifierSet("==1.*").to_range() | SpecifierSet("==1000000.*").to_range()
    >>> far.to_specifier_set() is None
    True

Limits of the model
-------------------

The pre-release opt-in is not itself a set. PEP 440 opts a whole specifier set
in when any one specifier names a pre-release, so ``&`` is requirement
conjunction (a comma-merge of the two specifier sets), not plain intersection,
on the opt-in. No model can keep that parity with
:class:`~packaging.specifiers.SpecifierSet`, exclusion soundness (an exclusion
grants no opt-in), and an involutive complement at the same time. This model
keeps parity and soundness, so complement drops the opt-in and a few Boolean
identities do not hold once an operand carries one.

A double complement therefore erases the opt-in rather than restoring it. ``~~r``
keeps ``r``'s versions but not its eager pre-releases:

.. doctest::

    >>> b = SpecifierSet(">=2.0b1").to_range()
    >>> list(b.filter(["2.0b1", "2.5"]))
    ['2.0b1', '2.5']
    >>> list((~~b).filter(["2.0b1", "2.5"]))
    ['2.5']
    >>> ~~b == b
    False

The erasure is still well behaved: ``~~~r == ~r``, both De Morgan laws hold, and
``r & ~r`` is empty.

.. doctest::

    >>> ~~~b == ~b
    True
    >>> (b & ~b).is_empty
    True

Because complement drops the opt-in while union keeps it, ``b | full()`` carries
``b``'s opt-in, which the full range (with no eager pre-release) does not, so it
does not collapse to :meth:`VersionRange.full`. For the same reason union no
longer distributes over intersection, and the absorption laws do not hold, when
an operand carries an opt-in:

.. doctest::

    >>> a = SpecifierSet(">=1.0").to_range()
    >>> b | VersionRange.full() == VersionRange.full()
    False
    >>> a & (a | b) == a
    False

Intersection still distributes over union, ``a - b`` agrees with ``a & ~b`` on
the version set and the opt-in region, and every law holds on the version set
as usual. The opt-in region makes the identities above differ only when a
specifier named a pre-release.

The arbitrary-string flag has corners of its own. ``SpecifierSet("")`` matches
even strings that are not PEP 440 versions, so its range,
:meth:`VersionRange.full`, admits them too; pass ``admit_arbitrary=False`` for
the versions-only full range. Only those ranges and ``===`` literals admit
such strings: combining ranges never grants an admission the operands did not
have, so ``~r`` stays version-only for any version-only ``r``. The flag rides
along where that is harmless, keeping ``~~full() == full()`` and union
idempotent, but an intersection or difference that shrinks the bounds does
not remember it:

.. doctest::

    >>> f = VersionRange.full()
    >>> "wat" in f
    True
    >>> "wat" in VersionRange.full(admit_arbitrary=False)
    False
    >>> ~~f == f
    True
    >>> (~f | ~f) == ~f
    True
    >>> (f & ~f) == VersionRange.empty()
    True
    >>> (f & ~f) == ~f
    False
    >>> d = f - SpecifierSet(">=1.0").to_range()
    >>> "wat" in (d | SpecifierSet(">=0.5").to_range())
    False
    >>> d == f & ~SpecifierSet(">=1.0").to_range()
    True

Reference
---------

.. automodule:: packaging.ranges
    :members:
    :special-members:
