# DSH Testkit Report

- Run: `20260818165208-998d550f`
- Verdict: **passed**
- Plugin: `dsh-aikube@0.1.0`
- Plugin digest: `sha256:ba5dd1e606c75be7c99d33b091fd1f013f190ca8f7c5b506e0ae7a8880bf20c5`
- DSH: `0.1.0-rc.6`
- DSH integrity: `sha256:9fbadbc72a258cf136d7883c688969eba212d1a505950f8234384a6da1cb1d52`
- Scenario: `aikube-cluster` (`quick/v1`)

## Lifecycle

| Stage | Status | Duration | Summary |
|---|---|---:|---|
| resolve | passed | 10070 ms | Resolved local-directory source |
| install-dsh | passed | 256210 ms | Installed exact DSH 0.1.0-rc.6 |
| package | passed | 272 ms | Packed dsh-aikube@0.1.0 |
| install-plugin | passed | 1366 ms | Installed dsh-aikube@0.1.0 into profile dsh-testkit |
| assemble | passed | 109 ms | Composed profile with 79 addressable rows |
| boot | passed | 6769 ms | DSH reached the boot runtime probe |
| register | passed | 0 ms | 2 runtime registration assertion(s) passed |
| exercise | passed | 0 ms | 1 deterministic tool exercise(s) passed |
| update | skipped | 0 ms | no updateFrom source |
| uninstall | passed | 376 ms | Removed dsh-aikube from the profile |
| reboot | passed | 957 ms | Profile rebooted without the plugin |
| recover | skipped | 0 ms | no recovery was required |
| cleanup | passed | 0 ms | Removed the owned run root and retained evidence |

## Assertions

| Stage | Assertion | Status | Message |
|---|---|---|---|
| resolve | source.reproducible | passed | Source resolves to an immutable run input |
| install-dsh | dsh.version.exact | passed | Installed DSH 0.1.0-rc.6 |
| package | subject.pack.digest | passed | Published tarball candidate has a content digest |
| install-plugin | install.manifest.dependency | passed | Plugin dependency is pinned in the profile manifest |
| install-plugin | install.manifest.bundle | passed | Plugin bundle is active in the profile |
| assemble | config.row.aikube | passed | Configuration row aikube is present |
| boot | boot.outcome | passed | Observed expected boot success |
| register | service.aikubeCluster | passed | aikubeCluster service is registered |
| register | tool.aikube | passed | aikube tool is registered |
| exercise | exercise.aikube.1 | passed | Tool aikube completed |
| uninstall | uninstall.manifest.dependency | passed | Plugin dependency was removed |
| uninstall | uninstall.manifest.bundle | passed | Plugin bundle was removed from the profile |
| uninstall | uninstall.package.files | passed | Plugin package files were removed |
| uninstall | uninstall.filesystem.residue | passed | No unexplained files remain after uninstall |
| reboot | service.aikubeCluster | passed | aikubeCluster service is absent |
| reboot | tool.aikube | passed | aikube tool is absent |
| cleanup | cleanup.owned-root.removed | passed | Owned run root was removed |
| cleanup | canary.log-leak | passed | Canary value did not appear in command logs |

## Observer Coverage

| Observer | Availability | Mode | Limitations |
|---|---|---|---|
| filesystem | available | owned-root snapshots | Changes outside the disposable run root are not observed |
| process | available | ps checkpoint | Short-lived processes between checkpoints may be missed; Local checkpoints share the host process and network namespaces |
| ports | available | listening-port checkpoint | Short-lived listeners between checkpoints may be missed; Local checkpoints share the host process and network namespaces |
| network | unavailable | unavailable | v0.1 does not include a network namespace trace or proxy observer |
| canary | available | raw-stream sentinel detection with sanitized persistence | Absence from process output does not prove absence of egress |

## Environment

| Key | Value |
|---|---|
| runner | "local" |
| isolation | "unsafe-local" |
| unsafeLocal | true |
| platform | "linux" |
| arch | "x64" |
| node | "v22.23.2" |
| pnpm | "unavailable" |
| image | null |
| imageId | null |

## Reproduce

```bash
dsh-test --dsh 0.1.0-rc.6 --runner local --suite quick --unsafe-local --config dsh-testkit.yaml
```

