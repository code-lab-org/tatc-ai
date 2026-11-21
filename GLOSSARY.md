# Mixed-instrument studies

A study estimates geometric access for specified instruments over a region and time window. These terms distinguish the modeled opportunities from actual observations.

## Language

**Instrument configuration**:
A set of modeling settings for one instrument on a specified spacecraft, including its operating mode, viewing geometry, applicable period, assumptions, and source revision. Different modes and revisions remain separately identifiable.
_Avoid_: Instrument name alone when referring to a specific configuration

**Geometric access**:
A modeled opportunity for an instrument to view a location under the study's stated assumptions. It does not establish that an acquisition occurred or that the measurement is suitable for a scientific purpose.
_Avoid_: Actual observation, usable snow observation

**Coverage envelope**:
A region that contains the modeled viewing footprints but may also include gaps the instrument does not view. An envelope estimate stays separate from totals reported as instrument access.
_Avoid_: Instrument access when the region includes unviewed gaps

**Geometry approximation**:
A declared simplification of an instrument's viewing geometry. Its limitations remain part of the study's interpretation.
_Avoid_: Validated geometry when only an approximation has been accepted

**Partial study**:
A study that models a stated subset of the requested instruments and identifies each omitted instrument and the reason for omission. An unmodeled instrument has unknown access, rather than zero access.
_Avoid_: Complete mixed-instrument result

**Ascending pass**:
A pass in which the satellite moves north.

**Descending pass**:
A pass in which the satellite moves south.

**Pass-direction filter**:
A study setting that includes only ascending or only descending passes for one configuration.
_Avoid_: Morning pass when the local time is not stated by the source

**Study export**:
A self-contained file recording a study's exact inputs, provenance, software versions, assumptions, and results.
_Avoid_: Run log, when the file is the durable study record

**Replay record**:
The exported inputs needed to run the same study again.
_Avoid_: Reproducible result, when no replay has been run

**Instrument catalog**:
A read-only external list of instruments, their carrying spacecraft, and their operating dates. OSCAR supplies it.
_Avoid_: Source library, configuration registry

**Configuration registry**:
The reviewed set of instrument configurations the server may select by exact ID and revision.
_Avoid_: Instrument catalog

**Source library**:
The reviewed set of documents and excerpts that configurations cite.
_Avoid_: Instrument catalog

**Document**:
One source at one revision, such as a web page, PDF, or notebook, with its title, publisher, URL, and retrieval time.
_Avoid_: Configuration

**Excerpt**:
A short passage copied from a document, with a locator and an ID that configurations cite.
_Avoid_: Full document copy

**Claim**:
A configuration's use of an excerpt, naming the evidence ID, excerpt reference, interpretation, and supported fields.
_Avoid_: Document
