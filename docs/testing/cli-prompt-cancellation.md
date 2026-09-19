# CLI prompt cancellation

`cli/test/commands/prompt-cancellation.test.ts` exercises the config and
interactive commands with scripted human input. Every script checks the prompt
kind, message and selection options, consumes exactly the expected prompts, and
asserts command dispatches, HTTP calls and saved configuration. Positive input
cases prove the same harness reaches real configuration writes and dispatches.

## Prompt inventory

| Command path | Boundary | Cancellation contract and positive control |
|---|---|---|
| `config init` | Reconfigure confirmation | Cancel and decline preserve existing bytes; accept proceeds to setup. |
| `config init` | API URL, API key, department text | Cancellation preserves existing bytes or leaves the file absent; accepted values are saved. |
| `config init` | Connection-test confirmation | Cancel and decline make no request; accept makes the health request. Setup was already saved before this prompt and remains saved. |
| `config set` | Field selection | Cancellation makes no file change; accepted selection reaches the value prompt. |
| `config set` | Value text for each field | Cancellation preserves the file or leaves it absent; accepted values update the selected field. |
| Interactive main menu | Action selection | Cancellation and explicit Exit stop successfully without dispatch. |
| Website scan | Scan-type selection and URL | Cancel or Back returns to the menu; cancelled URL dispatches nothing; valid basic/AI inputs dispatch the corresponding command. |
| Document scan | Document-type selection and path | Cancel or Back returns to the menu; cancelled paths dispatch nothing; PDF, PPT and LaTeX selections have positive dispatch controls. |
| Media scan | Media-type selection and path | Cancel or Back returns to the menu; cancelled paths dispatch nothing; image/video selections have positive controls. |
| Code scan | Path | Cancellation dispatches nothing; accepted input dispatches the code scan. |
| Evidence report | Optional department | Cancellation dispatches nothing; empty input intentionally uses the default, and nonempty input passes the department. |
| Settings | Action selection and API URL/key/department text | Cancel or Back does not dispatch; accepted values reach the real config command and temporary file. |
| Settings setup | Delegated setup prompts | Cancelled reconfiguration preserves bytes; accepted setup writes its values. |
| Profiles | Action selection and create/delete/use name | Cancel or Back does not mutate profiles; accepted names have create/delete/use controls. |
| Profile creation | Optional API URL | Cancellation does not create a profile; empty input intentionally uses localhost, and a supplied URL is preserved. |
| Profile list, config show/validate, help | Continue acknowledgement | Cancellation returns to the menu without repeating the preceding operation. Completed operations are not undone. |

## Execution and limits

From `cli/`, run `bun run test` for the full suite, or
`node node_modules/mocha/bin/mocha.js --forbid-only test/commands/prompt-cancellation.test.ts`
for these contracts alone.

The helper runs in an isolated Node subprocess with native experimental module
mocking enabled only for that subprocess. It imports the installed library's
real `CANCEL_SYMBOL` and `isCancel`; only text/select/confirm input is scripted.
This keeps ESM mocks out of the other tests and requires no TTY or new dependency.
The CI Node 22 runtime supports the module-mocking flag.

Configuration commands execute against real temporary files. Snapshots taken
at cancellation prove later code leaves the saved contents unchanged. HTTP
requests and non-configuration command dispatches are recorded at their
boundaries; these tests do not execute scans/reports or contact a service.
They verify command behavior after the prompt library returns cancellation,
not terminal rendering or the library's physical Escape/Ctrl-C handling.
