# Archived standalone CLI workflows

These are historical copies of the standalone CLI workflows formerly stored in
`cli/.github/workflows/`. They are inactive and retained byte-for-byte for
reference. The `.yml.txt` extension distinguishes this archive from runnable
workflow configuration. Do not re-enable or copy these workflows into
`.github/workflows/`; their release and publication paths are obsolete.

| Archived file | Former purpose |
| --- | --- |
| [onPushToMain.yml.txt](onPushToMain.yml.txt) | Generate CLI documentation, tag a version and create a release on pushes to `main` |
| [onRelease.yml.txt](onRelease.yml.txt) | Publish the standalone CLI package after a release |
| [test.yml.txt](test.yml.txt) | Run the standalone CLI test matrix |

The repository-root workflows are authoritative:

- [ci.yml](../../../.github/workflows/ci.yml) runs CI.
- [release.yml](../../../.github/workflows/release.yml) coordinates releases.
- [publish-npm.yml](../../../.github/workflows/publish-npm.yml) publishes the CLI through the coordinated release process.

Follow the [release integrity policy](../../RELEASE_INTEGRITY.md) and
[protected release and channel guidance](../../deployment/alpha-releases.md)
for signed tags, release approval, protected environments and publication gates.
