# Synthetic PDF profile controls

These four authored fixtures complement the unchanged 26-case `latex_research`
corpus for #454. They use the repository's AGPL-3.0 license and contain no real
institutional or personal documents. `runtime.json` pins their source hashes,
the compiler packages and the independent validator implementation.

S01 is the positive text/math control. S02's alternative describes its actual
single-arrow diagram. S03 explicitly identifies its simple first-row headers;
it does not supply guessed relationships for the grouped P03 research table.
S04 exercises a small `titlesec` heading configuration to observe the package's
behavior under each profile. Source bodies remain unchanged between profiles.

Authored descriptions and header associations are inputs, not Aelira-generated
repairs. No fixture represents human or assistive-technology acceptance. See
[the experiment and evidence boundaries](../../../docs/testing/latex-pdf-profiles.md).
