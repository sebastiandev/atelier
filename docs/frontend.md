# Frontend

Vite + React 18 + TypeScript. Dev server on `127.0.0.1:4173` (5173 conflicts on the user's machine), proxying `/api/*` to the backend on `8001` with WS upgrade enabled — same-origin, no CORS.

> Read [`architecture.md`](architecture.md) for the cross-layer rules and the seam definitions.
> Read [`design-system.md`](design-system.md) for visual conventions (brand mark, section headers, cards, icons, time formats).

## Layout

```
frontend/src/
├── App.tsx              # path-based router (no router lib)
├── Home.tsx             # /
├── WorkView.tsx         # /works/<slug>
├── ProjectScreen.tsx    # /projects/<slug>
├── Chat.tsx             # /chats/<slug> + spotlight composer + context doc modal
├── AgentView.tsx        # /agents/<slug> — wraps AgentTile in page mode
├── Connections.tsx      # /connections — CRUD UI for ConnectionStore
├── AgentTile.tsx        # the unit; "page" or "tile" mode
├── NewWorkDialog.tsx    # POST /api/works (with optional project picker)
├── NewProjectDialog.tsx # POST /api/projects (name + glyph + 7-swatch hue + default conns)
├── NewAgentDialog.tsx   # POST /api/works/<slug>/agents
├── MarkdownText.tsx     # react-markdown + remark-gfm + shiki wrapper
├── RichMarkdownEditor.tsx # rendered markdown with inline section editing
├── ShellTopbar.tsx      # shared wordmark, breadcrumbs, utilities, and primary action
├── LoopMode.tsx         # standalone goal → loop setup → staged run/result surface
├── LoopUI.tsx           # reusable loop selector, library, and structure editor
├── LoopRunView.tsx      # reusable staged Planning run/report/result primitives
├── useAgentStream.ts    # WS hook with replay + reconnect backoff
├── state/               # narrow Zustand stores (frontend-local concerns)
│   ├── theme.ts         # dark/light/ansi cycle, persisted
│   ├── tweaks.ts        # accent hue + layout choice
│   ├── closed.ts        # per-work set of agents pinned to the rail (closed)
│   └── layout.ts        # persisted resizable rail/dock widths
├── ThemeToggle.tsx      # sun/moon button driving useThemeStore
├── TweaksPanel.tsx      # accent hue slider + layout segmented control
├── connectionFields.ts  # per-source form schema (CONNECTION_FIELDS)
├── api.ts               # typed fetch wrappers + types + persona constants
└── styles.css           # tokens + every component style (one file, by design)
```

State: Zustand for frontend-local presentation concerns (see [State](#state)).

## Routing

Hand-rolled in `App.tsx`. Path prefix → component. We don't ship a router because:

- The route table is still a small prefix switch (`agents`, `works`, `projects`, `chats`, `connections`, and `settings`) plus Home.
- No nested routes, no parameterized search, no transitions.
- Adding `react-router` would be more code than the router itself.

If routing grows beyond ~5 patterns, swap it in.

Works have one persisted mode and one stable URL: `/works/<slug>`. `WorkView`
selects Manual, Planning, or Loop from `work.mode`; one-shot creation setup is
passed through `sessionStorage`, not route parameters. Legacy Works without a
mode keep the content-based fallback.

## Exploratory chats

`Chat.tsx` owns the chat route and the reusable chat surfaces. Home, Project, and Work each render a Chats section and bind `C` to open the spotlight `ChatComposer`; Home starts unlinked, Project presets `grounding={kind:"project"}`, and Work presets `grounding={kind:"work"}`. `grounding` is the Project/Work link that decides where the chat appears; `working_directory` is the optional folder used as the provider cwd. Home exposes both controls, Work hides the link because the current Work is implicit, and Project only allows the current Project or one of its Works. Home only lists unassigned chats, Project only lists project-grounded chats that have not moved into a work, and Work lists work-grounded/promoted chats. After creation, project-grounded chats land on their Project list, while work-grounded chats navigate to `/works/<slug>?chat=<CHT>` and WorkView opens the chat tile in the canvas. Project chat rows use the subtitle grounding layout so the associated work/project label stays directly under the chat title on wide screens. The composer uses the existing provider descriptors from `GET /api/providers`; provider/model and any non-default permission, mode, or effort option are persisted with the chat. Model-specific effort values/defaults come from descriptor `model_meta`, matching `NewAgentDialog`.

`/chats/<slug>` uses the `shell-v3 narrow-left` two-column layout: left rail for grounding/model/provenance, right column for the transcript and composer. The page fetches REST metadata with `GET /api/chats/{slug}` but renders and sends turns through `useAgentStream(chatSlug, { resource: "chats" })`, which opens `WS /api/chats/{slug}/stream`. Promotion is the single summary modal path: the user confirms name, brief, and project, then `POST /api/chats/{slug}/promote` returns the new Work and the UI navigates there. WorkView keeps promoted chat context folders in the work record for agent seeding/mounting, but does not render them in the Shared folders rail; the matching `ChatTile` shows an **Open chat seed** header button that opens `ContextDocModal`, which reads `GET /api/works/{work}/chat-contexts/{folder}/{filename}` and links back to the source chat. The full chat rail also exposes a neutral **Compact context** action that calls `POST /api/chats/{slug}/compact`.

`NewWorkDialog` is the single work-creation entry point: title, description, project, and Manual/Planning/Loop mode setup live in one modal. The selected mode is persisted on the Work, while legacy Works without one retain the content-based fallback. Planning submissions create the work through the normal `POST /api/works` path, stash a short `PlanningStartSeed` in `sessionStorage`, and navigate to `/works/<slug>`; WorkView consumes that seed once and starts the Planning chat with the selected framework, profile, work folder, plan files folder, and chat model options. Loop mode loads definitions from `GET /api/loops`, lets the user select one or choose **Create a new loop**, then stashes that choice with the required work folder in `LoopStartSeed` before navigating to the same stable Work URL. `LoopMode` opens the selected definition or new-definition editor and owns the later goal, run parameters, launch, and staged result surface. The definition owns reusable instructions and context; the Work-owned Loop brief adds task-specific notes, file/folder/URL/note context, and per-stage execution overrides without changing that definition. Manual submissions use the same Work URL without a setup seed.

Inside WorkView, planning is a durable work mode rather than a canvas card. Persisted mode decides the surface; only legacy Works without `mode` use the existing content-based fallback. **Plan this work** reuses `NewWorkDialog` instead of rendering a separate empty Planning page: the current Work identity remains visible, Planning stays selected, and the Manual and Loop cards are present but disabled. Before that modal opens, and again before it starts Planning, WorkView loads `GET /api/works/{work}/completion` and blocks the transition when any managed agent workspace has uncommitted/untracked files or cannot be inspected. The shared worktree inspector omits symlink-only status entries, so project shares and dependency symlinks do not block Planning. The Work remains in its current mode until `POST /api/works/{work}/planning-chat` succeeds.

The Planning modal lets the user choose framework, profile, work folder, plan files folder, and Planning agent provider/model/effort/permissions before starting. Starting Planning checks `POST /api/works/{work}/plan/framework-status`; if the selected framework is missing, the UI asks whether to create a setup chat. No stops the flow. Yes calls `POST /api/works/{work}/planning-setup-chat`, focuses the normal visible setup chat, and lets the existing chat runtime stream installer output/permissions from the selected folder. When the framework is ready, the backend creates/reuses one real work-grounded chat titled `Planning`, builds the first prompt, stores the selected folder as the chat working directory, and persists the selected planning setup in SQL. Before source docs exist, `PlanningMode` shows that real chat centered with no plan rail or right dock, while the left rail shows the setup/discovery/ready/materializing timeline. The **Create source plan** action is hidden until the chat stream or REST summary reports `planning_readiness.ready`; then it calls `POST /api/works/{work}/plan` with the Planning chat slug only. That POST resolves root/framework/profile/provider/model/options from the persisted PlanningSession, starts/resumes the background materializer, and returns status immediately; WorkView polls `GET /api/works/{work}/plan/materialization-status` every three seconds until complete. The center materialization panel shows the five latest meaningful activities and reuses `PermissionApprovalDialog` for every unresolved tool approval returned by the status endpoint, resolving decisions through REST while the backend polls the durable transcript. One quiet minute surfaces a stalled warning without sending input to an active provider. The rail remains phase-only, and Retry replaces the stale background task and provider session while retaining generated files and transcript. The backend runs or reuses the internal write-capable materializer, parses its `atelier_plan_materialization` metadata report, and returns the source-backed plan. The UI reads document content only when opening/editing a specific artifact. After materialization, `PlanningMode` renders the handoff-style shell: left rail with static Planning badge, progress bar, outline tree, source documents, and footstrip; center overview/detail/source/approve views; and the same `Planning` chat reused as the right dock. The dock collapses the discovery transcript behind a previous-conversation button by default so it opens as a ready revision chat. Typing `@` in that Planning chat opens a compact plan-document picker sourced from the current `plan.artifacts`; selecting a row inserts `@relative/path.md` into the plain chat message, matching the backend-seeded prompt contract. The reserved Planning chat is hidden from the normal Work chats rail/canvas so it does not appear twice. The surface is a projection over the flat `/api/works/{work}/plan` payload, deriving the epic outline and status buckets client-side.

`plan.planning_path` is the Atelier-owned state folder (`~/Atelier/works/<WRK>/planning`). Editable source artifacts live under `plan.plan_artifacts_path`, which defaults from the backend framework definition but can be overridden in Planning setup, and artifact `path` values are relative to that folder.

Mode screens use `ShellTopbar.tsx` for one stable 48px app header: the rail-aligned origin lane contains the Atelier wordmark and project/work breadcrumbs, the optional view lane contains merged full-view identity and status, and primary plus search/settings/theme actions stay right-aligned. The shared search action opens the same app-level modal as `Cmd/Ctrl+K`; screens do not own a separate topbar search path. Clickable parent crumbs replace header back arrows. The Loop library/editor merge identity and actions into this bar; Settings sections and run headers keep their Inter content titles in the canvas. Dense tile toolbars remain inside their owning surface. Mode identity belongs at the top of the left rail below the bar; rails never repeat the app wordmark or breadcrumbs.

**Complete Work** is the same Work-level action in Manual, Planning, and Loop mode. Its dialog loads `GET /api/works/{work}/completion`, summarizes retained agents and workspace state, and preserves workspaces by default. Removing workspaces is an explicit option enabled only when every listed git worktree is inspectable and clean; dirty or uninspectable state explains why removal is unavailable. Completing archives the Work into a read-only surface with transcripts and history intact, and **Reopen** restores editing and launch actions. Completed Works never show agent/chat/run mutation controls, and active runs must be resolved before completion. `AgentTile`, `ChatTile`, and staged-run transcript surfaces request replay-only streams in that state; the backend also derives replay-only behavior from Work status so opening archived history cannot wake a provider or recreate a removed worktree.

Home, Work/Loop, Planning, the Planning chat dock, and the Loop editor/run inspector are resizable through one `PaneResizeHandle`. Arrow keys move by 16px (Shift by 32px), double-click restores the surface default, and widths persist in `state/layout.ts` (`localStorage["atelier:layout"]`). Defaults and clamps are Home 500 (360–560), Work/Loop 280 (240–520), Planning 296 (248–420), Planning chat 420 (340–620), and the Loop inspector 420 (340–620).

Artifact and source views still use the same source-backed plan endpoints: selecting a story/spike/bug loads `GET /api/works/{work}/plan/artifacts/{id}`, editable source saves go through the hash-checked `PUT`, and plan approval calls `/plan/approve`. Plan approval is the initial baseline action for the generated source snapshot, so the Planning header, epic view, and approval-related story blockers expose an Approve plan action until the baseline exists. Once a baseline exists, saving a story/source edit through the UI updates that file's approved hash immediately; external file changes still surface as `changed` until the user saves or approves the latest snapshot. Launching from a ready executable artifact uses the existing `NewAgentDialog` and passes the artifact Markdown as a normal `file` context; after the agent is created, `POST /plan/artifacts/{id}/runs` starts a `run-NNN`, sends the backend run prompt, and schedules background monitoring. While the plan overview reports running artifacts, WorkView polls the plan so run state updates without a manual collect-report action. The inspector shows backend loop state (`loop_status`, reason, and findings), dependency launch blockers, proposal diff review, and tracking links; the left rail `+ bug` action opens the handoff-styled bug modal with problem text, completed/in-review story picker, optional Sentry link, and optional context URL before posting to `/plan/artifacts/{id}/bugs`. `blocked_user` reports expose a `Mark resolved` action that posts to `/runs/{run}/resume`; accepted runs use `/runs/{run}/accept`, which releases provider runtimes while retaining the agents, transcripts, and shared workspace for the run history.

The reusable-loop launch path supersedes the old NewAgentDialog handoff above. A story's first right-side action is **Start work**, which opens `LoopSelectorDialog`, defaults to Atelier Reviewed, and can open the loop library/editor without losing the target. Planning visibly injects the source story and structured report contract as read-only slot bindings; an optional per-story note is the only editable brief input and never gates launch. In Planning and Loop mode, plain **Save** writes a Work-owned overlay while **Save to library** creates a reusable copy; Settings writes the reusable library directly. Each agent stage can inherit or override provider, model, effort, provider-published Fast mode, and permissions, and file/folder context uses the shared filesystem picker. Settings asks for a temporary reference working folder before that picker opens and persists only the selected relative path, so reusable definitions remain portable. The backend creates stage agents after the selected definition and required context pass validation. `LoopRunView.tsx` owns one `RunSurface` and one `RunRail` for Planning story runs and standalone Loop runs: both show the pinned definition/revision, stage spine, runtime-derived live activity, structured output, failure recovery, and resizable read-only loop/transcript docks. Review return edges show their resolved automatic/human gate in setup, editor, run strip, and inspector. The rail no longer carries the loop's stage chain or its return edge: the run's own spine shows that topology with live state on it, so the rail held a second static copy competing with the run for attention. The rail keeps the loop name, scope, pinned revision and **view stages & config**. A human gate replaces the generic blocker card with selectable findings, an optional next-pass instruction, **Send back**, and **Approve as is**; unchecked findings are recorded as waived. **Retry stage** preserves the current run and workspace; **New run** opens setup with the latest saved definition plus the previous run's goal, folder, execution settings, and brief. Saving **Edit loop → new run** follows that same setup path with the new revision selected. Starting from either setup sends the source run id so the backend retains its exact dirty workspace while pinning the selected revision. A `waiting_report` run uses the warning status/card to say that no transcript activity has arrived for five minutes while the provider remains live; a hard timeout uses the failed-state Retry action. Every non-user-blocked stage failure keeps that Retry action visible even while an execution occurrence is selected, and shows the failed pass's last complete or partial agent message plus its transcript error. The spine is an execution ledger rather than a mutable definition list: every completed report remains a selectable occurrence, backward transitions append the re-entered stage with its pass number, and long histories wrap onto another row. The PR panel's meta line links the head commit (`pr.head_sha`, shortened to 7 characters) — the same commit Atelier cites when it replies to an addressed comment, so the reply is verifiable without leaving the run. The PR panel keeps every comment it has seen: threads created after the latest push are open and selectable, while ones already sent to Implement or predating that push fold into an `N earlier comments` section badged `sent to implement` or `before latest push`. They used to be dropped from the projection entirely, so pushing made a reviewer's comments disappear — which reads as data loss even though the comment is still on GitHub. Plan polling runs until every run has settled rather than only while one is `running`: approving a review is precisely what ends `running`, so the old gate tore the interval down at the moment there was something new to show and the transition only appeared after a manual refresh. The poll also leads with one immediate fetch. Unresolved stage-provider permissions reuse `PermissionApprovalDialog` inline and switch the run badge to a permission-waiting state; decisions use the existing agent WebSocket so the same turn and workspace continue. Planning replaces the plan outline with the story anchor and run history while a run is open. **Request changes** and **Approve result** live on the run surface footer only, for story runs and standalone Loop runs alike — `RunActions` gates them on handler presence, never on the `variant` prop, so the two modes cannot drift. `useProviderDescriptors` enriches the OpenCode descriptor with `GET /api/providers/opencode/models` (backed by the `opencode models` CLI), so every consumer — run setup, the new-agent dialog, chats — offers real models rather than the `configured-default` placeholder the static descriptor ships. A missing CLI leaves the placeholder rather than erroring, since it is only a problem for users on that provider. Setup shows nothing configurable until a loop is chosen — no auto-selection, so the picker is plainly the first decision and the stage sections appear in response to it. Arriving from a previous run is the one case that pre-selects. The pane scrolls in `.pm-setup-body` so the rail, topbar and chat dock stay put, and `.loop-brief-setup` centres itself at `min(960px, 100% - 56px)` in both modes. The planning chat dock hides while setup is open. Choosing a loop is the first section of run setup, not a modal before it: `LoopPicker` (search + list) renders inline and every section below reconfigures from the selection. Arriving from **Start new run** or **Change loop** pre-selects the previous run's loop, which can then be changed in place. The picker dialogs both modes used for this are gone, and `onChangeLoop` is replaced by `onSelectDefinition`. **Start new run** behaves the same in both modes: it returns to run setup seeded from the run it was launched from — loop, brief and stage overrides — where the loop can be reconfigured or swapped before starting, rather than opening the loop editor or a bare picker. `LoopBriefSetup` is the one setup surface for both; it takes no mode flag. A caller that owns the goal or the working folder simply withholds `onGoal` / `onChooseFolder` and the field renders read-only, the same handler-presence convention `RunActions` uses. Planning passes the story title as a fixed goal (`goalLabel="Story"`) and the plan root as a fixed folder. **Create PR** moved the same way: it is a run-surface action (`openCreatePr`) for story runs and standalone Loop runs alike, so the PR stage, its lifecycle panel, comment refresh, and PR-feedback follow-up pass all live next to the run that owns them. The story's Latest run card links into the run (**Review result**) rather than carrying its own copies; duplicating them there is what previously left story runs with no way to approve, since the footer buttons were `variant === "loop"` only. The story inspector and the plan rail show the PR of each story's latest run that opened one, read from `artifact.runs[].pr`. Listing every run's kept a superseded run's pull request on screen indefinitely: nothing refreshes a finished run's PR state, so a cancelled run's PR stayed frozen at `open` while the story's current PR was already merged — the plan `tracking` links they used to filter on are only written by `link_tracking`, which no frontend path calls, so both panels were permanently empty. WorkView polls the selected artifact while the overview reports an active run, so task, review, check, and approval transitions update without opening agent transcripts.

Current-stage prompts, warnings, failures, and live events are scoped to that
exact execution occurrence. Selecting a historical occurrence shows only its
persisted report; repeated passes never borrow the current pass's stream.
After acceptance, the one-off Create PR action and follow-up chooser belong to
the final approval occurrence and hide while other stages, including Create PR,
are inspected. A human **Approve as is** decision preserves the original review
report for audit history while rendering that review occurrence as completed.
The approval panel renders the same sectioned `StageReport` every stage
occurrence uses — summary, acceptance criteria, findings, changed files,
evidence, divergences — rather than a bare summary paragraph. It sources that
report from the last occurrence that actually filed one, since the approval
stage is a human gate and never reports; it drops the `.doc` reading measure
and loosens row spacing, because the run's whole verdict is read on one screen
here. The structured fields were always persisted and previously discarded at
render, which is why agents wrote entire reviews into `summary`.
Loop-stage `artifact_recorded` stream events bump the Work artifact revision,
so the left rail refetches newly created PRs without a page reload.

The Agent tab in the loop structure inspector edits reusable approved command prefixes. The first agent stage is labeled as the loop default; later stages inherit until changed. Expanded Loop setup shows the same list per stage and writes an optional Work-only replacement with **Reset to inherit**, so run-specific approval changes do not silently revise the loop library. Completed human-check review occurrences also render the exact findings selected for implementation and the optional user instruction; automatic review transitions omit that decision section.

The shared run footer exposes **Open in editor** beside **Open transcript** as soon as the active stage workspace resolves. It uses the configured editor descriptor and remains available during implementation, review, blocked, failed, and accepted states while that workspace path is retained. Active standalone and Planning runs also expose **Cancel run** there; cancellation preserves the run workspace and transcript.

Standalone `LoopMode` reuses those same definition and run primitives with a freeform goal instead of a Planning artifact. Its brief-first setup compresses goal and definition above one row per stage: agent rows show immutable template instructions, Work notes, context origins, and execution resolution; checks and approvals stay slim. The first agent stage establishes the run provider/model/effort/permissions, later agents inherit unless overridden, and required note slots disable Start inline while optional blanks run on template instructions alone. Legacy agent stages without a note declaration expose the same optional Work brief. The latest draft autosaves to the Work and each started run pins its own copy. Structural edits still open `LoopStructureEditor`; promoting Work input into a reusable loop is an explicit fork/revision action. After launch, the rail gains durable run history and chats; the main area shows the current stage's output below the stage spine. Only the chronological current pass is expanded by default; previous passes remain folded until explicitly expanded, then expose every stage occurrence for output selection. **Open transcript** targets the selected execution occurrence's exact agent slug and sequence range: the active occurrence reuses its existing stream, historical fresh-session occurrences load their own transcript through a replay-only right dock, and explicit session reuse still displays only the selected pass. The time beside the active pass starts at that occurrence's stage prompt rather than inheriting the aggregate run duration. **Discuss in chat** immediately creates an idle Work-grounded chat and opens its `ChatTile` in that same resizable dock; it never opens the new-chat spotlight or navigates away. The chat uses the run worktree and stage provider settings, while the selected occurrence summary, findings, validation evidence, generated artifacts, and changed files are hidden seed context rather than a submitted message. Its persisted discussion marker keeps the loop-authority warning visible and removes Start work and handoff controls. It does **not** remove the permission dialog: the dock passes `role: "advisory"` when it creates the chat, and that role is prompt-on-every-action rather than sandboxed read-only (`backend/src/domain/chats/posture.py`), because plan/read-only postures end by requesting permission to exit planning — a prompt a suppressed dialog can never answer, which blocked the turn on the unanswered ACP call forever. The no-implementation boundary is the discussion system prompt plus that visible approval, so the chat can write scratch files while the loop stays authoritative over the codebase. Provider-published model and effort controls remain available. Chat Fast toggles use the persisted provider option while reconnecting and switch to the live session capability once advertised, so Planning and discussion docks do not lose the control between streams. Accepting or cancelling releases provider runtimes but keeps the agents, transcripts, and shared workspace; the UI has no per-run cleanup action. Accepted results replace the generic rerun action with **Apply feedback** and **Verify current state**: amend requires a note and re-enters the first task stage, while verify skips task stages and enters the first review/check. Both continue the same branch/worktree and retain kind/seed labels in run history.

The loop library also owns reusable stage definitions. The add-stage picker merges Atelier's global stage library with the selected working repository's catalog; repository definitions win duplicate IDs, while each entry retains its catalog origin for later editing and deletion. Linked loop stages retain source revision provenance, edits become loop-local overrides, and users may open the source or detach it. **Start blank** opens the shared stage editor in loop context: Save/Keep local inserts an inline stage, while Save to library persists it and inserts a linked revision. The main column is a document surface for instructions, stage-kind inputs, compact run-time bindings, and bundled context; the continuous right dock holds identity, execution defaults, session, permissions, semantic outcome capabilities, and advanced policy. It never wires outcome destinations. Standalone injected inputs are read-only; loop-context inputs are editable, bundled files/folders use the filesystem picker, and inline notes travel with the stage into each run. A `pr` stage and the accepted-run **Create PR** dialog share one saved setup and launch a fresh write-capable agent in the retained run worktree. Once opened, the latest PR occurrence contains the status/check/review panel and background plus forced comment polling. Inline review comments render as one readable root with nested replies; external-latest threads are expanded, counted, and selectable, while viewer-latest threads remain visible but collapsed, marked **you replied**, and unselectable. Selected threads and optional instructions start another implementation pass; later PR occurrences and source-based follow-up runs update the same branch and PR, while sealed passes retain their own reports, push timestamp, and addressed-comment list. A terminal standalone run offers **Change loop -> new run**, which returns to setup with the existing goal, brief, execution settings, and retained workspace while leaving the completed or cancelled run pinned to its original loop revision.

Loops and stages import/export from the library (spec: `docs/loop-import-export.md`). Export is a per-card `DownloadIcon` action (object actions stay on the card): it fetches `GET /api/{loops,stages}/{id}/export` and turns `{filename, content}` into a client-side `Blob` download — the only blob-download path in the app. Import is a collection action beside **Create loop** / **New stage**: a hidden `<input type=file>` reads the file text (no multipart) and opens `LoopImportDialog` / `StageImportDialog`, a scrim+modal review surface. The dialog previews via `POST /api/{loops,stages}/import/preview` (re-previewing on name edits so the derived id and collision update live), renders one row per stage with the shared `data-stage-kind` tint and an `inline`/`link-clean`/`link-conflict` tag, exposes Replace/Use existing/New-id controls (Replace disabled for built-in-backed stages and warning with the blast-radius count), and gates the primary action behind an explicit "reviewed the commands and permissions" checkbox whenever the safety surface lists `approved_command_prefixes` or `write`. Confirm posts to `POST /api/{loops,stages}/import` and refreshes the library. `LoopUI.tsx` owns both dialogs and the `downloadTextFile` helper; `api.ts` owns the typed wrappers and preview/plan types.

Plan artifact/source documents use `RichMarkdownEditor`: it hides leading YAML front matter from previews, renders the body as sectioned markdown through `MarkdownText`, and lets each heading section be edited inline through an icon button. Esc cancels an active section edit, and the outer Save/Reset controls still go through the same plan artifact/source endpoints; hidden front matter remains byte-for-byte intact when body sections are saved.

Inside WorkView, the left rail orders Shared folders first (project shares plus generated chat seed/context folders), Active agents second, Chats third, and a PR-only Pull requests section last.

WorkView's Work hero action row owns lifecycle commands. `Mark done` remains the non-destructive completion path; the trash icon opens `DeleteWorkDialog`, requires typing the work slug, and calls `DELETE /api/works/{slug}` for permanent removal of the work, linked work chats, agents, artifacts, handoffs, and worktrees.

Chat rows are rail controls rather than navigation links: clicking a work chat opens a fixed-accent `ChatTile` in the same sortable canvas as agent tiles; closing the tile only removes it from the canvas, and the chat remains in the Chats rail. Chat rows can be renamed by double-clicking the title and deleted through the kebab menu, matching agent row affordances. Chat tiles use the chat websocket stream for transcript/input/stop/permission behavior and reuse `AgentTile` transcript units plus `TurnMetricsBar`, but intentionally omit IDE, console, reveal-worktree, detach, persona controls, duplicate header compaction, and current-work grounding metadata. Full-page chats and chat tiles also show a compact composer context gauge from the latest `turn_metrics` snapshot so context pressure remains visible near the input. Their context-bar compact action uses the same chat compact endpoint as the full chat page. Across full chats, docked chats, and manual agent tiles, an active turn with no stream event for five minutes shows a non-modal stalled banner; **Reconnect runtime** stops only that surface's provider runtime and lets its websocket resume the stored session without automatically resending input. Their start-agent action calls `POST /api/works/{work}/chats/{chat}/context`, then opens `NewAgentDialog` with the returned `context.md` as a normal `file` context.

A stage's timeout is edited in the same block as its provider, model, effort and permissions (`Execution defaults` in the stage editor), not under **Advanced** — a timeout chosen without seeing the model is guesswork, and a wrong one costs a full retry. Advanced keeps the retry limit. A deterministic check renders the timeout with no agent controls beside it, since it runs none. A user approval has a `timeout_minutes` in its data but no editor, here or before this change: the stage parks the run waiting for a person and monitoring stops, so the value is inert.

**The timeout is editable per run.** `design/Loop Brief Handoff.html` §02's rule is that a timeout never renders away from the agent and effort that determine it, so it appears in the run-configuration block as well as in the stage editor. The stage owns the default; a brief may raise or lower it for the work in hand via `LoopStageBrief.timeout_minutes`, which is applied to the definition *before* the run snapshots it (`briefs.with_brief_timeouts`) — so the pinned snapshot is the loop that actually ran and the monitor reads the timeout like any other stage's. **Save** is gone from the loop editor: only **Save to library** remains, because a Work-scoped save is no longer written.

Earlier passes in the stage spine fold into a single line, not one line each. Each pass already hid its stages, but a run that has been round the loop six times still stacked six lines above the pass being worked on, which on a short screen is the only part worth seeing. The fold line carries the pass range, stage count, review returns, elapsed and cost for the group, and opens back into the per-pass lines (with a **fold** control to collapse them again). A pass the user expanded by hand is never folded away underneath the summary, and the range label is dropped when that leaves the folded set non-contiguous.

A run's requests for changes are a section of the **implementation stage's report**, above Summary and collapsed by default, not a run-level panel. The pairing is the point: what a pass was asked to change sits immediately above what it reported doing about it. `requestsForPass` narrows the run's `feedback[]` to the requests one pass was working from — opened against that pass or earlier, attempt-scoped retry notes only for the pass that carried them, and nothing an earlier pass already settled. Other stages do not show it; the implementation stage is one click away. Record bodies are reduced before display (`cleanFeedbackText` strips the `<!-- pr-commenter -->` marker, fenced blocks and link targets, since a PR comment's raw Markdown can run to hundreds of characters of query string), clamped to three lines, with the unreduced text on the row's `title`. **Already dismissed on this run** follows it in the same report, reduced and clamped the same way and likewise collapsed. It is run-wide rather than per-pass, because a dismissal stores only its text and cannot be attributed to the pass that was running when it was made; it sits beside the requested changes because it answers the same question — what this stage was and was not asked to do. Neither panel appears on the run surface: run-level state docked below the stage body reads as belonging to whichever stage is on screen. `LoopRunInspector` shows a stage's declared `reports` beside its declared `inputs`, each required/optional and resolved or not, plus the stage's run-history level; resolution is checked against the run's stages, because a declared report names another stage.

Run setup's **◈ Run configuration** is one outlined card per brief, per the handoff: Agent (provider · model · Fast), Effort, Permissions, Allowed command prefixes, Timeout — one labelled row each rather than a single line of controls. Effort and Permissions are segmented rather than dropdowns, built from the values the selected provider and model publish, so a model offering no effort field renders no row. `PlanningAgentControls` takes `layout="rows"` for this; `NewWorkDialog` keeps the inline row, which suits a surface that is mostly about something else. A stage that inherits shows the resolved values rather than one truncated line — effort and permissions were previously invisible there, and they are what decide a run's cost and blast radius.

A plan document open in the centre reloads itself when the planning chat edits it. The signal is the agent's own tool calls -- `Edit` / `MultiEdit` / `Write` are canonicalised across providers and carry a path, so no marker and no polling are involved (`src/editedPaths.ts`). Two constraints shape the wiring: it keys on `tool_result` rather than `tool_call`, because the bytes land when the tool returns and reacting to the call reads the old file; and the supervisor allows one subscriber per chat, so `WorkView` cannot open its own stream — `ChatTile` reports finished writes upward through `onFilesEdited`. A refresh only replaces the editor draft when the user has not typed, the same rule the run poll uses.

Links between plan documents open in place. `resolvePlanLink` resolves a relative href against the linking document's own path and matches it to an artifact; the click then selects that artifact, which also moves the rail selection. Left to the browser these resolved against the app URL, left the route table and fell through to the home screen. `MarkdownText` takes an optional `onLinkTo`, so only the plan viewer changes: transcripts and loop instructions keep plain external links. An absolute URL, an anchor, or a relative path the plan does not own stays inert rather than navigating away.

Run-stage discussions are owned by their stage entry rather than the Work rail. **Discuss in chat** keys the chat by Work, run target, run, and stage, so repeated clicks reopen the same right-dock session; standalone Loop mode no longer renders a separate Chats rail section.

## Projects

Projects are an **optional** grouping above Work. The whole feature is data-light by design — Project is metadata (name, glyph, single OKLCH hue, optional default Jira/Sentry connection slugs), not a workspace.

- **Home** (`Home.tsx`) renders a Projects card grid above a flat Latest-work list. Each card shows a glyph, a big proj-color active-count, the 3 most-recent works (status icon + slug + title; row clicks navigate to the work and `e.stopPropagation()` so the card click doesn't fire), connection pills derived from the project's defaults, and an `Open ›` arrow. The card itself is a `<div role="button">` (not an `<a>`) because the recent rows inside are anchors and nested anchor is invalid HTML — clicking the card calls `window.location.assign("/projects/<slug>")` programmatically. A peer LooseCard (no glyph color, no conn pills, no arrow) shows works with no project; clicking it filters the latest list to loose-only.
- **Latest work** has its own filter pills (All / per-project tinted via `--proj-h` / Loose with count) and a Tiles/List view toggle (default **list**, persisted under `localStorage["atelier:home:view"]`). The pills are independent of card-click navigation — clicking a project pill scopes the list without leaving Home; clicking a project card opens the project page.
- **`ProjectScreen.tsx`** at `/projects/<slug>`: hero with a `linear-gradient(180deg, --proj-soft, transparent)` wash + 3px tinted top bar (`.proj-hero-bar`), 56×56 glyph (panel-bg + proj-line border + proj-color text), 24px name, slug + description, `+ New work in {Project}` CTA on the right. Below the hero: meta grid (ID, Default connections with conn-pills + a small ↗ to /connections, Active count in proj-color, Completed count in proj-color), `home-tabs`-style Active/Completed strip, right-aligned Tiles | List toggle (default **tiles**, persisted per-project under `atelier:project:{slug}:view`). Tiles renders the existing `.work-card` grid with a "Start new work in {Project}" tile first; List renders the flat `.work-list`.
- **`NewWorkDialog`** accepts `projects`, `presetProjectSlug`, `lockProjectSlug` props. On Home it seeds the picker from the active filter; on Project pages it sets `lockProjectSlug=true` so the picker is disabled. Selecting a project tints the modal via `--proj-h`.
- **`WorkView`** breadcrumb adds a project crumb when `work.project_slug` is set: `Workspace › [glyph] {Project} › WRK-...`. The project crumb is a real link to `/projects/{slug}`.

### Per-project color tokens

Cards / chips / pills set `--proj-h` (hue 0–360) inline via `style`. The token system in `styles.css` derives the rest:

```css
[style*="--proj-h"] {
  --proj-bg:   oklch(0.62 0.18 var(--proj-h));         /* solid: glyph + dot */
  --proj-soft: oklch(0.62 0.18 var(--proj-h) / 0.14);  /* card top bar wash, chip bg */
  --proj-line: oklch(0.62 0.18 var(--proj-h) / 0.4);   /* chip border, hover line */
}
[data-theme="dark"] [style*="--proj-h"],
[data-theme="ansi"] [style*="--proj-h"] {
  --proj-soft: oklch(0.62 0.18 var(--proj-h) / 0.20);
  --proj-line: oklch(0.62 0.18 var(--proj-h) / 0.5);
}
```

Loose work uses the neutral tokens (`--bg-2`, `--line`, `--fg-3`) so the same shell renders without project styling.

## Topbar shape (shared)

`ShellTopbar` is the canonical app chrome on Project, Work, Planning, Loop, Chat, Agent, and Settings screens. It is a 48px `--bg-1` band with no drawn border: a rail-width origin lane, an optional truncating full-view identity lane, and a right action cluster. `view={{ title, detail, inline, onBack }}` is reserved for identities the handoff explicitly merges into app chrome; ordinary section titles stay in content. Project breadcrumbs set only `--proj-h`; the cascade derives the swatch. Home intentionally keeps its handoff-specific two-pane workspace composition, but uses the same compact brand mark and global utility vocabulary.

### UpdateChip

`UpdateChip.tsx` lives in every topbar (Home, ProjectScreen, WorkView) just before `TweaksToggle`. It polls `GET /api/update-status` every 10 minutes; when the backend reports `available=true` it renders a small accent-tinted pill ("Update available"). Clicking it reveals a popover with the repo path and a single primary action that copies `cd <repo> && ./atelier update` to the clipboard — the chip is a hint, not a self-acting upgrader. It points at the CLI rather than an agent session because updating a checkout is a procedure, and routing `git pull` through an LLM is not an improvement; this matches how Claude Code and Codex prompt for their own updates. Self-updating from inside the app is deliberately not offered — the backend would be replacing the code it is running. Dismiss persists in `sessionStorage` keyed on the upstream SHA, so the chip reappears when a new upstream commit lands even if previously dismissed.

## AgentTile — modes

`AgentTile` is the same component in two contexts:

- `mode="page"` (`/agents/<slug>`): full-viewport, fixed `880px` max-width, no persona pip in header, no maximize control surface.
- `mode="tile"` (inside `WorkView`'s canvas): no fixed height, persona pip + agent name in header, persona-tinted top border via `--p-color`/`--p-soft`, maximize toggle that sets `position: fixed; inset: 1.5rem`.

The mode is a structural switch, not a theming switch. Splitting into two components would duplicate the streaming logic; one component with a mode prop is right while behavior is 95% shared. If divergence grows past the textarea + header, split.

### Header layout — 3-cell grid

`AgentTile` header is a CSS grid with `grid-template-columns: 1fr auto 1fr`:
The shared header markup lives in `TileHeader.tsx`; AgentTile, ChatTile, and Planning Mode pass context-specific left/meta/right slots so each surface can keep its own controls without hand-rolling another header. Chat tile chrome uses `ChatTileSurface.tsx` (`ChatTileFrame`, `ChatTileTranscript`, `ChatTileComposer`) so planning-chat variants compose the same frame instead of copying it.

- **Left** cell: persona pip + status dot + h2 title. h2 truncates with `text-overflow: ellipsis` so long names don't push the meta off-center.
- **Center** cell: `agent-slug` (mono) + `provider-pill` (`amp · rush`) + `conn-status` (`CONNECTED`) + a `folder-pill mono` showing `shortenPath(worktreePath)`. Left-click reveals the worktree in Finder; **right-click opens a small context menu** (`.folder-pill-menu`, anchored at cursor coords) with two options: *Open worktree* and *Open Atelier folder* (the per-agent dir under `~/Atelier/works/<work>/agents/<agent>/` — transcript, agent.json, contexts/). Backend dispatch via `POST /api/agents/{slug}/reveal?kind=worktree|atelier`. Center stays horizontally centered regardless of how wide the title or controls clusters get; the 1fr columns absorb the slack equally.
- **Right** cell: `tile-controls` wrapper with **open-in-IDE / handoff / maximize / detach / close** `.btn.icon.sm` controls. The open-in-IDE button uses the selected editor descriptor from `GET /api/settings` (`url_template` plus path tokens such as `{path_uri}` / `{path_param}`) — browsers route unknown protocols to the OS handler without navigating, so the page stays.

The standalone worktree-icon button (formerly between conn-status and tile-controls) was removed — the folder pill is itself the reveal affordance. Path display shortening lives in `pathFormat.ts` for reuse by AgentTile/Chat surfaces without importing WorkView.

### Session Model And Effort Selectors

When the active provider advertises a mutable `model` session config option, Agent and chat composers render a compact `Model: <current>` selector in the composer action row. Opening it sends `session_config_refresh` and shows the shared `SessionModelPicker` searchable popover because OpenCode model lists can be large; `session_config_options` seeds/refines the choices and current value, `session_config_changed` updates after a successful switch, and replay rebuilds the same state after reconnect. The selector is disabled while a turn is active so changes apply cleanly to future prompts. Providers without an advertised model option keep the existing static model display and do not show the composer control.

Agent and chat composers also render compact live option selectors when the provider advertises supported config ids: `thinking_effort`, `reasoning_effort`, or ACP's generic `effort` become **Effort**, and Codex ACP's `fast-mode` becomes **Fast**. These controls use the advertised choices/current value rather than hard-coded frontend enums, are disabled while a turn is active, and persist accepted changes so future resume/detach paths keep the user's selection.

### Composer Image Paste

Agent and chat textareas accept pasted clipboard images. The shared frontend helper uploads png/jpeg/gif/tiff/webp files through `POST /api/fs/uploads/images`; work-scoped surfaces include the work slug so files land under that work's attachments folder. It first reads normal paste file items, then falls back to the async Clipboard API from the Cmd/Ctrl+V keydown path for macOS screenshot pasteboards that do not expose files on the paste event. Generic clipboard names such as `image.png` / `image.tiff` are treated as alternate encodings of one bitmap and collapse to the preferred representation so a single screenshot does not create duplicate `[Image N]` markers. Pasting inserts an immediate inline `[Image N]` marker in the textarea. Agent tiles append the returned paths as removable `file` contexts and allow sending with only the attached context. Chat surfaces keep the marker visible and append labeled file paths only to the sent message, because chat websocket frames do not currently accept context attachments.

### Context Compaction

`AgentTile` derives context pressure from the latest `turn_metrics.last_prompt_tokens` snapshot plus the provider/model context window (`frontend/src/AgentTile.tsx`). The status row above the composer shows the current git branch or `DETACHED HEAD` first, then latency, token usage, `ctx N%`, and the activity label. The composer carries the visual state: a 2px top-edge context gauge fills to the current percentage, and a 2px bottom-edge persona rail sweeps while the agent is working. At 75% the row shows a warn-colored inline **Compact** button; at 86% the context label, gauge, button, and modal primary action switch to the critical tone. Clicking **Compact** opens a blurred, outcome-led confirmation modal that snapshots the current context/tone, stays open while the async compaction runs, shows the current phase and elapsed time from `compaction_progress` events, then shows success or a retryable error. At 100% normal sends and tile actions are blocked: the modal opens automatically, can be dismissed so the user can inspect the last response, and reopens on the next attempted action; it can offer handoff when the parent supplied `onHandoff`.

This is intentionally not styled like a tool permission prompt. Permission prompts stay inline above the composer and use tool/security language; compaction uses context/cost language and composer-edge rails because it changes the provider session behind the same agent. The frontend calls `POST /api/agents/{slug}/compact` through `api.compactAgent`; the supervisor kicks the active websocket on session replacement so `useAgentStream` reconnects and replays the `context_compacted` transcript marker. Reconnects are generation-guarded so stale sockets from the compaction race cannot replace the current live subscription. `AgentTile` renders that marker as a session boundary: the previous transcript units collapse into a disclosure, and **View summary** lazy-loads the saved compaction doc via `GET /api/agents/{slug}/compactions/{filename}`. Provider-side automatic compaction uses `provider_context_compacted` instead; it renders as an informational boundary without a summary button because Atelier did not create a local compaction summary.

Every agent-backed surface mounts `TurnMetricsBar` unconditionally rather than gating it on having metrics or an active turn — `AgentTile`, `ChatView`, and `ChatTile` alike. The bar is a fixture of the composer, and gating made it appear as a turn started and vanish once the turn ended or the socket blinked, so the branch and context readings a user goes looking for *between* turns were exactly the ones not rendered. `TurnMetricsBar` already returns an em-dash placeholder row when `metrics` is null (`AgentTile.tsx:2609`), so the slot stays put and fills in. Note that the git branch segment still needs a `turn_metrics` event to exist: it comes from that event's `git_branch`, so a brand-new agent shows the placeholder row without a branch until its first turn lands.

Chats do not become agents and do not use the AgentTile context-pressure modal.
`ChatView` and `ChatTile` reuse `TranscriptUnits` and `TurnMetricsBar`; when the
websocket replays a `context_compacted` event, the shared boundary lazy-loads
`GET /api/chats/{slug}/compactions/{filename}` through a chat-specific summary
loader.

## View-toggle pattern

`Tiles | List` segmented control used on both Project and Home. Implementation pattern:

- Two `.view-toggle-btn` siblings inside a `.view-toggle` flex container with a 1px border and a 1px divider between buttons.
- Default per surface: Project = **tiles**, Home = **list** (chronological feed reads faster as rows).
- Persistence keys: `atelier:project:{slug}:view` per project; `atelier:home:view` site-wide. Read on mount via a dedicated `readPersistedView(slug?)` helper that handles private-mode failures and falls back to the surface default.
- The `.proj-tabs` container variant adds `margin-left: auto; margin-bottom: 0.5rem` to keep the toggle aligned with the underlined tabs above; on Home (where neighbours are pills, not underlined tabs) the toggle uses the base `align-self: center` only.
- Button padding is tuned to `3px 9px` (matches `.filter-pill`) so toggle and pills sit on the same baseline when they share a row.

## `useAgentStream`

`useAgentStream(slug, { resource })` is the single point of contact with supervisor-backed websocket streams. The default resource is `"agents"` (`/api/agents/<slug>/stream`); chat surfaces pass `{ resource: "chats" }` for `/api/chats/<slug>/stream`. It returns `{ events, status, sendInput, sendStop, sendPermission, sendSessionConfig, loadOlder, history, pendingPermissions }` and handles:

**Cursor-based resume.** Within a session, on WS close + reconnect it appends `?cursor=<lastSeq>` so the server replays only the window we missed before going live. The server's replay-then-live semantics guarantee no duplicates and no gaps (see `backend.md` → WS protocol). The cursor lives in a closure-scoped `lastSeqRef` and resets to `0` on every fresh mount — the transcript itself isn't persisted client-side, so seeding non-zero on mount would yield an empty tile (the bug that retired the old `atelier:cursors` localStorage key).

**Bounded initial replay.** Fresh mounts pass `replay_limit` so very long transcripts, such as `agt22`'s 170 MB log, do not freeze the browser before the render cap helps. The hook default remains 100 for chat surfaces; `AgentTile` requests a 500-event ceiling, then renders an adaptive window based on the latest server sequence: 500 through sequence 1,000, 250 through 5,000, 100 through 10,000, and 50 beyond that. If the boundary falls inside a contiguous message/thinking delta run, the tile retains that loaded run so streamed text never loses an earlier prefix. The hook treats the server's `history_state` frame as metadata, not a transcript event; `AgentTile`, `ChatView`, and `ChatTile` show **Load older**, which calls `loadOlder()` and prepends `GET /api/{agents|chats}/{slug}/transcript?before_seq=<oldest>&limit=<limit>` chunks. The backend may also replay sticky session-config metadata outside the visible tail so the model/effort/fast controls render before the next user input; local transcript caps ignore those invisible metadata rows when counting hidden lines. This keeps React state, permission scans, metrics scans, and first-send/focus work bounded to the loaded window instead of the whole on-disk transcript.

**Paint-batched stream events.** Incoming WS frames are buffered for 50ms before appending to React state, with an immediate flush on socket close. The append is scheduled with React `startTransition`, so transcript painting is lower priority than typing into the controlled composer textarea while an agent is streaming. `lastSeqRef` still advances as each frame arrives, so reconnect cursors stay exact while dense token streams avoid one React/Markdown/scroll pass per tiny delta.

**Exponential reconnect backoff.** Schedule: `1s → 2s → 4s → 8s → 16s → 30s` (cap). Resets on a successful `onopen`, so a single transient blip costs one 1s retry — only consecutive failures walk the ladder.

**Terminal close on 4404.** When the backend closes with code 4404 the slug is unknown to the server and the hook sets `status: "stopped"` and exits the retry loop. With the supervisor's resume path (see `backend.md` → WS protocol), a backend restart no longer surfaces 4404 — the WS handler rebuilds the adapter with the persisted `session_id` so the conversation resumes mid-stream. 4404 in practice means the slug doesn't exist (e.g. stale localStorage in the closed-rail state, or a deleted chat).

**Provider authentication recovery.** `providerAuth.ts:29` derives an active sign-in requirement only from enriched `authentication_required` errors. Later assistant/thinking/tool/turn output clears historical failures; `useAgentStream.ts:418` also exposes a local confirmation watermark so a repeated failure opens a fresh gate without storing state or sending a websocket action. `ProviderAuthPrompt.tsx:3` is shared by agent tiles/pages and chat tiles/pages; while visible, each existing composer-disabled rule blocks input without clearing the draft or replaying the failed submission. After confirmation, provider controls stay disabled until the next input frame is sent, preserving lazy provider startup.

**Close = pin to rail.** The X on `AgentTile` is "close" — `WorkView` records the slug in `useClosedStore` and unmounts the tile. The WS connection ends; the agent row + `transcript.ndjson` + provider session ID stay on the server. Clicking the rail entry restores the tile, which mounts a fresh `AgentTile`, which opens a new WS, which resumes the same provider session by ID. There is no "delete" — closing is fully reversible by design.

**Esc = stop the current turn.** Inside the composer, plain Esc while the agent is producing a turn sends `{"type":"stop"}` over the WS. The supervisor records a `user_stop` transcript line and calls `adapter.stop_turn()`. Modifier+Esc combinations (Shift/Cmd/Ctrl+Esc) are reserved for "exit maximize" so they can't fire stop-turn. Esc on Amp agents currently no-ops at the adapter layer, but the user's intent still lands in the transcript.

## Connections page

`Connections.tsx` at `/connections`. One section per `ConnectionType` (`jira`, `sentry`, `honeycomb`), source-tinted via `data-source` (the same token system that drives context rows / connection chips). Each section lists existing connections as expandable cards with a Disconnect/Re-verify/Save footer, plus a "+ New" inline form.

**Form schema is declarative.** `connectionFields.ts` maps each source type to a `ConnectionSchema` of `{ id, label, placeholder?, required?, secret?, options? }` entries — same shape as the design source's `CONNECTION_FIELDS`. The grid renders selects for fields with `options`, password inputs (with reveal toggle) for `secret: true`, plain inputs otherwise.

**Verify flow.** New-connection Verify is the only network action that hits the server: first click POSTs the row + verifies; subsequent clicks PATCH the same `con-N` slug + re-verify. The Save button is just "close the form" — the primary action only enables once `verifyState === "ok"`, so the form can't close around an unverified row, and Save never re-POSTs. Re-verify on an existing card writes any pending token rotation via PATCH before calling `/verify` so the keychain has the latest.

**No token round-trip.** The `Connection` type returned by the API has no `token` field; the UI tracks token edits in form-local state only and sends them via POST/PATCH. The reveal toggle on the secret input shows whatever the user just typed, never anything fetched from the server.

## Composer

Multi-line auto-growing textarea, capped at 200px before scrolling. Enter submits, Shift/Cmd/Ctrl+Enter inserts a newline.

**Optimistic "thinking" status on send.** The status dot flips to `thinking` the moment you click Send, instead of waiting for the next `status_change` event from the adapter. Storage is `thinkingSinceSeq: number | null` — captures the latest event seq at Send time so a *new* progress event (any of `status_change`, `turn_metrics`, `error`, `message_delta`, `message_complete` with a later seq) clears it. The boolean precursor only watched for `status_change` as the *last* event, which got shadowed by intervening deltas — and Amp drops the trailing `status_change("idle")` on short turns, leaving the dot stuck. The seq-based gate plus the multi-event terminal set is the fix.

**Composer placeholder.** "Agent is working — Esc to stop" appears only when the agent is actively producing output, derived via `isAgentActive(events)` off the *last* event's nature (active = `message_delta` / `thinking_delta` / `tool_call` / `tool_result` / `user_input` / `status_change(thinking|live)`; everything else = inactive). Same fallback rationale: the cumulative `agentStatus` lies on Amp short turns; the last event tells the truth. Active-looking replay tails older than five minutes are treated as stale so an interrupted stream does not pin reopened agent/chat surfaces in "thinking"; `client_error` advisory websocket frames also clear optimistic send state without advancing the server replay cursor.

## Streaming + grouping

`AgentTile.groupEvents()` collapses runs of `message_delta` into a single growing assistant `RenderUnit`, and runs of `thinking_delta` into a single thinking unit. This is what lets you see text accrete instead of seven separate chunks. A non-delta event (`tool_call`, `status_change`, `user_input`, …) closes any pending delta unit and starts its own.

**`tool_call` and `tool_result` are paired.** When a `tool_result` event lands with a `tool_id` matching a `tool_call` already in the unit list, it's folded into the call's render unit (`unit.result`) rather than appearing as a sibling line. Orphan `tool_result`s (no matching call — replay races, suppressed-call edge cases) still render as standalone units.

**Per-tool renderers.** A `TOOL_RENDERERS` dispatch maps each canonical tool name (see `backend.md` → "Canonical tool shape") to a specialised view:

- **`Bash`** — collapsed summary echoes the command (`▸ Bash · git diff`); body has the description, `cwd`, the command in a syntax-highlighted code fence, and the paired result (auto-unwraps Amp's `{output, exitCode}` JSON).
- **`Edit` / `MultiEdit`** — collapsed summary shows `path · +N −N`; body renders a line-level diff via `diffLines` from the `diff` package, syntax-highlighted per side via `codeToTokensBase` from Shiki using the language inferred from the file path. Open by default since the diff *is* the body.
- **`Write`** — `path · N lines` summary; body shows the content fenced with the inferred language.
- **`Read` / `Grep` / `Glob`** — single-line view (`▸ Read · ~/…/foo.py · L1-100`) collapsing to `<details>` only when there's a result to fold in.
- **Fallback** — unknown tool names render the args dict as a JSON code fence (today's behaviour).

Folding policy: `<details>` is **closed by default**, opens automatically when the result content is a unified diff (`isUnifiedDiff` matches `diff --git` / `@@ -.. +.. @@`). `Edit` / `MultiEdit` always open since their *call* body is a diff.

**Result rendering.** `ToolResultBody` parses Bash results as `{output, exitCode}` (Amp's wrapper), surfaces `exit N` next to "result" when non-zero, and detects unified-diff content to render with `<UnifiedDiffView>` — which extracts the file path from `diff --git a/PATH b/PATH` so the lines also pick up syntax highlighting via Shiki. Falls back to plain `<pre>` for other content.

**Slash commands (`/`).** When a session advertises commands (`session_commands`), the composer offers a picker: type `/` at position 0 and `useCommandPicker` (`CommandPicker.tsx`) renders the list, with ArrowUp/Down to move, Enter/Tab to accept, Esc to dismiss. All three composers use the one hook — the agent tile, the chat tile (which is what planning chat and loop discuss both render), and the standalone chat route — because two composers with differently-behaved pickers would be worse than none. Three rules matter. The trigger is stricter than `@`: position 0 only, closing on the first space, so `src/foo` and `and/or` never trigger it. Esc stays reserved for stop-agent — the picker consumes it *only while open*, exactly as the `@` mention branch does. And send is guarded by `unknownCommandName`: providers parse the slash themselves and silently no-op a name they don't know, producing an empty turn indistinguishable from a hung agent, so an unadvertised name is refused inline instead. Gating is on `commands.length`, never on a provider name — a session that advertises nothing gets no picker and sends slash text verbatim.

**ACP event granularity (STORY-033).** ACP providers stream richer events; all handled in `groupEvents`/`renderUnitFor` with zero provider branches: `plan_update` reuses the todo-list row (`parsePlanEntries` — full-replacement semantics, each update pushes a fresh snapshot like TodoWrite does); `tool_call_update` folds a live `status` into the matching tool card (never a row of its own); `tool_result.diff` is a structured `{path, old_text, new_text}` that `DefaultToolCallView` renders through the real `DiffView` even when the tool name isn't canonical (codex-acp's opaque args land here); `mode_change` renders as a status line. `session_commands` and `session_config_options` / `session_config_changed` are transcript events but intentionally not visible transcript rows; `AgentTile` consumes them to render mutable controls such as OpenCode's refresh-on-open, searchable live model selector. `PermissionApprovalDialog` renders the `pendingPermissions` queue from `useAgentStream`: one pending request shows the single approval card, 2+ requests switch to the grouped queue with Allow all / Reject all convenience actions, and every bulk action still sends one `permission` WS frame per request. Permission buttons stay generic (`Allow`, `Always allow`, `Reject`) while ACP option kinds drive the actual response; agent-named option labels are only used as fallbacks/tooltips and to infer the capability name when older events carry a human action title as `tool_name`. Session cost prefers the provider-reported cumulative `turn_metrics.cost_usd` (latest wins — never summed) over token-math estimates (`computeSessionCost`). Unknown event types still fall through `renderUnitFor`'s `default: null` — older builds ignore newer events by construction.

## Design tokens

Lifted from `design/design_handoff_atelier/design_files/styles.css` (gitignored — keep it in sync if it changes upstream).

**Dark is the default at `:root`; light is opt-in via `[data-theme="light"]`.** `App.tsx` mirrors `useThemeStore.theme` onto `<html data-theme=...>`, and the override block in `styles.css` swaps the background ramp, foreground ramp, lines, status hues, and shadow stack to light values. Persona tints are intentionally untouched — the same hue reads on both themes. The `<ThemeToggle>` component sits in the Home + WorkView topbars and flips the store; the choice persists under `atelier:theme` in `localStorage`.

**Token families** (all in `frontend/src/styles.css` `:root`):

- Background ramp: `--bg`, `--bg-1`, `--bg-2`, `--bg-3`, `--panel`
- Foreground ramp: `--fg`, `--fg-2`, `--fg-3`, `--fg-4`
- Lines: `--line`, `--line-soft`
- Status hues: `--good`, `--warn`, `--danger`, `--info`
- Accent: `--accent`, `--accent-soft` (focus glow), `--accent-line` (focus border), `--accent-fg`
- Shape: `--radius-tag` / `--radius-ctl` / `--radius-card` = 2px, `--radius-tile` = 3px, `--radius-input` = 6px, `--radius-pop` = 8px
- Floating layers only: `--shadow-pop`
- Controls: `--ctl`, `--ctl-sm`; chrome typography: `--chrome-*`
- Document reading: `--doc-size`, `--doc-leading`, `--doc-fg`, `--doc-measure`
- Fonts: `--font-ui` (Inter w/ system fallback), `--font-mono` (JetBrains Mono w/ system fallback)

## Persona theming

Each persona owns a hue. Components opt in by setting `data-persona="<id>"` on a wrapping element; descendants pick up `--p-color` and `--p-soft`:

```css
[data-persona="architect"] { --p-color: oklch(0.78 0.14 20); --p-soft: oklch(0.78 0.14 20 / 0.14); }
/* developer / product / ux / writer ... */
```

The five canonical personas are listed in `frontend/src/api.ts` (`PERSONAS`, `PERSONA_GLYPH`). Writing `var(--p-color, var(--accent))` gives a sensible fallback when no persona is in scope.

This is what powers: the `AgentTile` tile-mode top border, the `WorkView` rail row's tint + accent bar when focused, the canvas-cell ring when a tile is selected, the persona-card hover/active in `NewAgentDialog`.

WorkView tile canvases use stretched grid rows (`frontend/src/styles.css`) so agent/chat tiles fill the available canvas height. Transcript caps should change internal scroll content, not the outer tile height.

## Dialogs — minimal-first pattern

`NewWorkDialog` is one compact creation flow. Identity fields come first,
followed by the Manual / Planning / Loop mode cards. Planning expands inline
setup for work type, framework, working folder, framework output folder, and
the Planning agent; Loop expands an inline searchable definition picker plus a
create-new choice, then captures the working folder and hands off to the
standalone setup screen. One **Create work** action ends every variant.

`NewProjectDialog` uses the same compact shell for name, optional description,
the seven project hue swatches, and an optional default repository folder.
New Work inherits that folder when the project is selected. The frontend
contract is ready, but project persistence does not store `default_folder` yet.

`NewAgentDialog` keeps name and branch in one row, then presents an explicit
workspace choice: an isolated worktree for parallel edits or the selected
folder shared in place. Working folder, provider/model options, goal, and
context follow in that order. Fork/fresh remains a separate starting-point
choice. The folder field opens `FolderPickerDialog`; branch uses
`BranchPicker`, which calls `GET /api/git/branches?path=<folder>` lazily and
caches until the folder changes (click-outside dismiss, autofocused filter,
Enter-to-pick when one match remains, Esc to close). A blank branch name means a
detached worktree at the repo's current remote default (see `backend.md` →
WorktreeManager); typing or picking a name creates that branch from the same
resolved base on agent start. Shared mode disables branch and starting-point
controls. The frontend already sends `workspace_mode`, but the backend must
still implement its shared-folder semantics.

Only `NewAgentDialog` owns per-agent `contexts`. Connection-backed entries
render through `ContextRow`; simple text, link, file, and folder entries render
through `SimpleContextRow`. The backend does not yet accept the simple `folder`
kind, so that option is a declared frontend contract until the context model and
renderer support it.

## ContextRow

`ContextRow.tsx` — the inline connection picker / creator used inside dialogs. Two modes:

- **pick**: dropdown of existing connections of the row's type + per-context value input (ticket key, event ID, etc). Sentinel `+ New <type> connection…` flips into "new".
- **new**: inline form with the same field schema as the Connections page (`connectionFields.ts`). Verify is the only network action; first click POSTs the row + verifies, subsequent clicks PATCH the same `con-N` slug + re-verify. Save commits the row to the parent dialog's connections list and the context's `conn_id`. Cancel from "new" with no fallback connections **and** nothing yet committed removes the row entirely — the headline cancel-removes-half-state interaction.

Parents own the connections array and pass it down. `ContextRow` calls `onConnectionSaved(connection)` once on save, so the dialog can dedupe and show the new connection in subsequent picker dropdowns. Verified state is reflected on the dropdown (`name · ✓`) so the user can tell at a glance which creds are confirmed.

## Provider-driven `NewAgentDialog`

The dialog renders its provider/model fields from the descriptors at `GET /api/providers`. The provider list is the backend's new-session surface (`NEW_SESSION_PROVIDERS`), so legacy runtime ids can remain resumable without appearing in creation. The primary field renders through a fixed-height searchable `ModelPicker`, which avoids native-select growth for long model lists and supports keyboard navigation. OpenCode starts with the `configured-default` fallback, then calls `GET /api/providers/opencode/models?refresh=true` when selected so connected provider models can be chosen before launch. New Chat uses the same picker/OpenCode refresh path and exposes permission/effort options inline from the shared descriptor helpers.

Advanced per-provider options (Claude's `thinking_effort`, `permission_mode`) render in a collapsible "Advanced" `<details>` block when the descriptor's `options` map is non-empty. Each entry becomes a labeled `<select>`; defaults seed from the descriptor and reset on provider change. Shared helpers in `providerDescriptors.ts` narrow matching effort selectors when `model_meta` publishes model-specific option values/defaults, and only coerce the current value if the selected model no longer supports it. New Chat only sends non-default option entries; New Agent keeps its existing behavior of sending the populated option dict when options exist.

## State

Component-local `useState` + the WS hook is the default. Cross-component, frontend-only state lives in narrow Zustand stores under `frontend/src/state/`, persisted to `localStorage` via the `persist` middleware where it should survive reload.

Current stores:

- `settings.ts` — `{ theme, editor, terminal, accentHue, editorOptions, terminalOptions }`, hydrated from backend `GET /api/settings` and written through with `PUT /api/settings`. The backend owns the selectable editor/console descriptors (`label`, `command`, `url_template`); the FE only renders them and interpolates path tokens for editor URL handlers. Legacy `atelier:tweaks` / `atelier:theme` localStorage blobs are migrated into the backend once on boot.
- `closed.ts` — `{ byWork: Record<workSlug, agentSlug[]> }`, persisted under `atelier:closed`. `WorkView` filters closed agents out of the canvas; clicking a closed rail entry restores the tile (which reopens its WS and resumes the provider session). Replaces the prior "minimized" model — there is no "delete".

**One home per value.** `LoopBriefSetup` renders the run's inputs and its caller starts the run from them, so anything the screen edits has to live in exactly one place or the two can disagree — the screen showing one setting while Start sends another. The goal and the base execution config both live on the `LoopBrief`: the goal is `brief.goal` (no separate `goal` state), and the provider/model/options are the entry stage's `brief.stages[entry].agent`, derived on read rather than mirrored into a caller-held `agentConfig`. Both were parallel states once, and both drifted. `LoopMode` and `PlanningMode` now hold only the brief.

When adding a new store: keep it narrow (one concern per file), put presentation-only state here (per CLAUDE.md → "UI state is frontend-local"), and don't reach into it from outside React-tree code unless you have a reason — `useStore.getState()` is a synchronous read, fine inside a `useEffect`.

## Build / typecheck

```bash
cd frontend
npm run dev        # vite dev server with HMR + /api proxy
npm run typecheck  # tsc --noEmit
npm run build      # tsc + vite build (writes to dist/)
```

The build emits per-language Shiki chunks loaded on demand (only languages Claude actually emits get fetched).

## Deferred

Items still on the Phase B / future-sprint list (kept here as a quick checklist; the source of truth is `_bmad-output/sprint-plan-atelier-2026-04-30.md`):

- [x] Cursor persistence (Zustand + `localStorage`)
- [x] Slow-subscriber drop policy in supervisor (backend)
- [ ] Transcript virtualization (only when a transcript actually gets long)
- [x] Light-theme tokens + theme toggle
- [x] `NewAgentDialog`: collapsible Advanced section for provider `options`
- [x] Inline context attachments in `NewWorkDialog` / `NewAgentDialog` (per-agent contexts: `text`/`url`/`file` plus connection-backed)
