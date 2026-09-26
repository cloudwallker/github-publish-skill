# GitHub Publish

### A Codex skill for preparing and publishing projects on GitHub

**Turn a local project into a reviewed GitHub publication, with bilingual documentation, project visuals, and your own contributor identity. Check files, the Git index, and outgoing commits before publishing.**

English | [中文](README_ZH.md)

[Highlights](#highlights) · [Quick Start](#quick-start) · [Usage](#usage) · [Preflight Audit](#preflight-audit) · [Documentation](#documentation)

![Workflow from local project through preparation, identity verification, three audit scopes, and GitHub publication](assets/workflow.svg)

*Workflow illustration, not an application screenshot. Publishing is performed by the agent; the included audit script is read-only.*

## Highlights

- **Prepare a focused publication.** Organize source files, documentation, and assets while preserving the project's existing structure. Exclude private configuration and development conversations.
- **Check three scopes.** Inspect candidate files, the complete Git index, and the final tree plus intermediate commits in the outgoing range. Deleting a file from the latest revision does not hide it from the history audit.
- **Keep contributor ownership.** Verify the authenticated GitHub account and commit attribution; prefer a confirmed GitHub noreply address. Preserve existing third-party credit.
- **Present the project in two languages.** Produce an English README, a matching Chinese README, a bilingual repository description, and suitable project visuals.
- **Handle source and releases separately.** Use an independent directory for a clean first publication; preserve history for updates. Versioned releases require an explicit request and a separate asset audit.

## Quick Start

### 1. Prepare the tools

Use Codex with skills support and the tools required for your task:

| Tool | Used for |
| --- | --- |
| [Git](https://git-scm.com/downloads) | Repository operations; index and commit audits |
| [Python 3.9+](https://www.python.org/downloads/) | Running the preflight script with the standard library |
| [GitHub CLI (`gh`)](https://cli.github.com/) | Account verification and GitHub publishing |
| [Gitleaks 8.19+](https://github.com/gitleaks/gitleaks/releases) | Secret scanning in every audit scope |

Obtain tools from their official sources. Authenticate GitHub CLI with `gh auth login`, then check the session with `gh auth status`. Do not paste tokens into a prompt.

### 2. Install the skill

Ask Codex:

```text
Use $skill-installer to install the skill at the root of
https://github.com/cloudwallker/github-publish as github-publish.
```

Codex detects installed skills automatically; if the skill does not appear, restart Codex. Installing the skill does not authorize it to publish a project. See the [official skill guide](https://developers.openai.com/codex/skills/).

### 3. Give it a concrete task

Open the project in Codex, then say:

```text
Use $github-publish to prepare and publish this project to my GitHub account
as a private repository named demo-project.
```

The agent prepares the publication, verifies identity, runs the required checks, and publishes within the authorized scope. Missing identity evidence or unresolved audit findings must be addressed before upload.

## Usage

| Task | Example request |
| --- | --- |
| Prepare without publishing | `Use $github-publish to prepare this project for GitHub. Do not upload it yet.` |
| Publish publicly | `Use $github-publish to publish this project as a public repository named demo-project.` |
| Update an existing repository | `Use $github-publish to push the current project changes to its existing GitHub repository.` |
| Publish a version | `Use $github-publish to publish version v1.0.0 with the reviewed release assets and bilingual release notes.` |

Defaults are **Chinese conversation**, **English-first project documentation**, and **private new repositories**. You can customize language and visibility preferences in [SKILL.md](SKILL.md), or specify them in your request. Existing repository visibility is preserved unless a change is explicitly authorized.

Ordinary publishing requests cover source files and necessary resources. They do not automatically create a Release, a PR, or a Pages deployment. The skill does not require Superpowers or install global hooks.

## Preflight Audit

`scripts/preflight.py` reads the project and scans temporary snapshots. It does not stage, commit, push, delete project files, or change configuration. You can run it independently of Codex.

From this repository's root:

```sh
python scripts/preflight.py --help
python scripts/preflight.py files --root ../demo-project --gitleaks gitleaks
```

`../demo-project` is a synthetic example path: replace it with the actual publication directory. Use a trusted Gitleaks executable on `PATH`, or pass its absolute path. Keep audit reports and review records outside the project.

| Scope | What is checked |
| --- | --- |
| `files` | The publication directory, or exact relative file paths in a JSON manifest |
| `staged` | Actual versions of all files in the Git index, including unchanged tracked files |
| `commits` | The final tree, intermediate trees, and commit messages within the explicit outgoing range |

For commit audits, set `--base` to the fetched remote SHA and `--head` to the fixed local SHA. Use `--base EMPTY` only for a new remote branch. Git scopes require the repository root and complete history. See [audit commands and review records](references/preflight.md) for full examples.

The script returns JSON with findings, an inventory, content hashes, resolved refs, and a fingerprint. It omits file contents and secret matches.

| Status | Exit code | Meaning |
| --- | --- | --- |
| `pass` | `0` | Automated checks passed for this snapshot |
| `blocked` | `1` | Blocking findings need remediation |
| `review_required` | `1` | Eligible findings need actual inspection and hash-bound review records |
| `incomplete` | `2` | Tools, inputs, coverage, or limits prevented a complete check |

**A machine pass is not a guarantee against every disclosure.** The agent must still verify privacy context, contributor identity, images, and presentation. Secrets, explicit process artifacts, and failed checks cannot be waived by manual review. Rerun affected scopes after changes; `--expect-fingerprint` detects changes against a previous audit of the same scope and inputs.

## Documentation

| Guide | Contents |
| --- | --- |
| [Publishing and identity](references/publishing.md) | Authentication, commit attribution, first publication, updates, and remote verification |
| [Content policy](references/content-policy.md) | Files to retain, exclude, or review |
| [Source layout](references/source-layout.md) | Light organization without unnecessary restructuring |
| [Presentation](references/presentation.md) | Bilingual READMEs, visuals, and layout verification |
| [Preflight](references/preflight.md) | CLI options, review records, scan limits, and asset audit alternatives |
| [Releases](references/releases.md) | Version tags, separate asset checks, release notes, and checksums |

The entry point is `SKILL.md`; `agents/openai.yaml` supplies skill metadata. `scripts/` contains the read-only auditor, `references/` contains workflow guides, and `tests/` contains regression tests using temporary synthetic repositories.

Audit limits include 20 MiB per file, 256 MiB per audit, 1,000 entries per archive, archive nesting depth 3, and 1,000 outgoing commits. ZIP, TAR, and GZIP are inspected recursively. Images require visual and metadata review; unsupported formats, external Git objects, and other incomplete coverage need separate handling. See the preflight guide before auditing large projects or release packages.

## Contributing and Attribution

Maintained by [@cloudwallker](https://github.com/cloudwallker). Keep English and Chinese documentation aligned, use synthetic test data, and preserve legitimate contributor and source attribution. Workflow source references are listed in the relevant guides.

The test suite requires Git, Gitleaks, and [Pillow](https://pillow.readthedocs.io/en/stable/installation.html) for the image fixture. With those available, run `python -m unittest discover -s tests -v`; see the [test setup](references/preflight.md#本地测试) to select a trusted scanner. Test cases cover index snapshots, historical findings, archives, redaction, fingerprint changes, and review boundaries.
