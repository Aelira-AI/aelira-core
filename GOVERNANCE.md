# Project governance

Aelira Core is the free, self-hosted open-source edition of Aelira. There is no paid feature tier in the core. Hosted infrastructure and support are offered separately through [aelira.ai](https://aelira.ai). The engine and dashboard use the repository's [AGPL-3.0 licence](LICENSE); the [CLI](cli/LICENSE) uses MIT.

## Maintainers

| Role | Account | Responsibility |
|---|---|---|
| Lead technical maintainer | [RD (Reg) Crampton](https://github.com/rdcrampton) | Technical direction, code review, security coordination and release decisions |
| Project administration account | [Aelira-AI-Dev](https://github.com/Aelira-AI-Dev) | Repository administration and maintenance operations |

These accounts serve the current maintainer; they do not represent two independent reviewers. Both retain administrator access. [CODEOWNERS](.github/CODEOWNERS) identifies them as the review owners for this repository.

Maintainers review proposed work, triage reports, manage releases and decide whether a change is ready to merge. They record material design decisions in issues or pull requests so contributors can understand the reasoning.

## Contributors

Contributors can help with code, documentation, tests, accessibility review, reproducible bug reports and deployment feedback. You do not need organisation membership or write access to participate. Start with [CONTRIBUTING.md](CONTRIBUTING.md), [Discussions](https://github.com/Aelira-AI/aelira-core/discussions) or an issue marked [good first issue](https://github.com/Aelira-AI/aelira-core/labels/good%20first%20issue).

GitHub records merged contributions in the repository history and [contributors graph](https://github.com/Aelira-AI/aelira-core/graphs/contributors). Maintainers can also acknowledge testing, documentation and accessibility review in release notes, with the contributor's consent.

## Access and review

Repository access is granted for a specific responsibility, rather than automatically after a contribution:

- **Community contributor:** participates through discussions, issues and fork-based pull requests; no privileged access is required.
- **Triage contributor:** may receive Triage access to organise issues and discussions once that responsibility is agreed.
- **Code contributor:** may receive Write access for ongoing implementation work. Changes still follow the pull-request process.
- **Maintainer:** may receive Maintain access after sustained, reviewed contributions and agreement on review and release responsibilities.
- **Administrator:** manages repository access, settings and security configuration. This access is reserved for project administration.

Maintainers agree on and document any new privileged assignment. Permission changes do not transfer copyright or change the project's licences.

Open a scoped issue before implementation and link it from the pull request, as described in the [contribution guide](CONTRIBUTING.md#issue-traceability). Maintainers review the diff, evidence and compatibility impact. CI checks are necessary evidence; a green result does not establish accessibility conformance or replace review of a remediated file.

The project currently has one technical maintainer. The workflow must allow that maintainer to merge their own pull requests after the required checks and documented verification. It must not imply an independent review that did not occur.

## Project decisions

Discuss open-ended proposals in [Ideas](https://github.com/Aelira-AI/aelira-core/discussions/categories/ideas). Agreed work becomes an issue with scope and acceptance criteria. The lead maintainer makes the final technical decision, taking contributor and user feedback into account. Proposals should explain the user need, evidence, maintenance cost and compatibility impact.

Changes to governance or licences need a public proposal and maintainer review. Contribution conduct follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Security reports follow [SECURITY.md](SECURITY.md) and must not be posted publicly.
