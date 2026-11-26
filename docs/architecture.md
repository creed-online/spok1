# spok1 architecture

These diagrams show the system described in [prd.md](prd.md). The PRD is the source of truth. Where the two disagree, follow the PRD and update this file. Section numbers such as §6 point to sections of the PRD.

Contents:
1. [System context](#1-system-context)
2. [Network and deployment](#2-network-and-deployment)
3. [Inside the VM](#3-inside-the-vm)
4. [How a request runs](#4-how-a-request-runs)
5. [Guardrails and approvals](#5-guardrails-and-approvals)
6. [The brain and sync between machines](#6-the-brain-and-sync-between-machines)
7. [The learning engine](#7-the-learning-engine)
8. [The auditor](#8-the-auditor)
9. [agentd: scheduler, watchers and notifier](#9-agentd-scheduler-watchers-and-notifier)
10. [Project OS](#10-project-os)
11. [Multi-project isolation](#11-multi-project-isolation)
12. [Authentication and connectors](#12-authentication-and-connectors)
13. [Discord layout and routing](#13-discord-layout-and-routing)
14. [Code packages](#14-code-packages)

---

## 1. System context

The whole system from the outside. Every LLM call runs on the unmodified `claude` binary under the Max plan. No API key is used anywhere.

```mermaid
flowchart TB
    subgraph Surfaces["Access surfaces"]
        Phone["📱 Phone<br/>Discord app"]
        Laptop["💻 Any computer<br/>Claude Code + guard hook"]
        ClaudeApp["Claude app / claude.ai<br/>Mem0 connector, Remote Control"]
    end

    subgraph VM["☁️ Azure VM (always on)"]
        Manager["Manager session<br/>+ Auditor + agents"]
        Agentd["agentd runtime<br/>(plain Python)"]
        MC["Mission Control<br/>(web, Tailscale-only)"]
    end

    subgraph Brain["🧠 The brain (shared everywhere)"]
        BrainRepo["brain git repo<br/>(curated)"]
        Mem0["Mem0 Cloud<br/>(facts & episodes, real time)"]
    end

    subgraph External["External services"]
        Discord["Discord<br/>private server"]
        GitHub["GitHub<br/>repos, CI, PRs"]
        Services["Connected accounts<br/>Canva · Sheets · Meta · Gmail · Shopify · YouTube"]
        HC["healthchecks.io<br/>dead-man switch"]
        Blob["Azure Blob<br/>backups, audit log, media"]
    end

    Phone <--> Discord
    Discord <-->|"gateway (outbound only)"| Manager
    Agentd -->|"REST: cards, channels, reactions"| Discord
    Laptop -->|"webhooks (send-only)"| Discord
    ClaudeApp -.->|"Remote Control"| Manager
    ClaudeApp <--> Mem0

    Manager <--> BrainRepo
    Manager <--> Mem0
    Laptop <--> BrainRepo
    Laptop <--> Mem0
    Agentd <--> BrainRepo

    Manager --> GitHub
    Agentd --> GitHub
    Manager --> Services
    Agentd --> Services
    Agentd -->|"ping every 5 min"| HC
    HC -->|"VM silent → alert"| Discord
    Agentd --> Blob
    Laptop -.->|"Tailscale"| MC
    Phone -.->|"Tailscale"| MC
```

---

## 2. Network and deployment

The VM has **no inbound ports**. Every connection is either outbound from the VM or goes through the Tailscale private network.

```mermaid
flowchart LR
    subgraph Tailnet["🔒 Tailscale private network"]
        LaptopN["Laptop"]
        PhoneN["Phone"]
        subgraph Azure["Azure: resource group"]
            subgraph VNet["VNet / NSG: no inbound rules"]
                VMN["Ubuntu 24.04 · B2s · 4 GB swap<br/>non-root user<br/>systemd: claude-manager, agentd"]
            end
        end
    end

    LaptopN -->|"ssh + tmux attach"| VMN
    LaptopN -->|"Mission Control (HTTP)"| VMN
    PhoneN -->|"ssh for /login renewal"| VMN

    VMN -->|"outbound 443"| Internet["Discord · GitHub · Mem0 · Anthropic (Max)<br/>connected services · healthchecks.io · Blob"]

    PublicIP(("Public IP<br/>outbound only")) --- VMN
```

| Unit | Runs | Restart policy |
|---|---|---|
| `claude-manager.service` | `manager-start.sh` → tmux → `claude --channels <discord plugin>` | `Restart=always` |
| `agentd.service` | `spok1-agentd` (uv) | `Restart=always` |
| `backup.sh` (timer) | `~/.claude` state + `learning.db` → Blob (Cool tier, 7 days) | nightly |

---

## 3. Inside the VM

```mermaid
flowchart TB
    subgraph ManagerBox["MANAGER session (tmux + systemd, /login)"]
        M["Manager<br/>route · recall · plan · dispatch · report"]
        AUD["Auditor<br/>pre-flight + post-run review"]
        GUARD{{"guard hook<br/>(PreToolUse, every tool call)"}}
        subgraph Core["Core agents"]
            R["researcher"]
            C["coder<br/>(own worktree)"]
            RV["reviewer"]
            T["tester"]
            L["librarian"]
            AA["agent-architect /<br/>org-designer"]
        end
        subgraph Proj["Project agents (per project)"]
            PA["leads + specialists<br/>e.g. marketing, sales, dev"]
        end
        M <--> AUD
        M --> Core
        M --> Proj
        Core --> GUARD
        Proj --> GUARD
        M --> GUARD
    end

    subgraph AgentdBox["AGENTD runtime (plain Python, setup-token for claude -p jobs)"]
        SCH["scheduler"]
        WAT["watchers"]
        NOT["notifier"]
        GOV["usage governor"]
        APR["approval broker"]
        LRN["learning engine"]
        AUDR["auditor jobs"]
        ORG["org: task board,<br/>work cycles, reports"]
        CON["connectors +<br/>token watcher"]
    end

    subgraph Data["Local data"]
        LDB[("learning.db<br/>events · episodes · jobs<br/>alerts · stats · audit")]
        PDB[("project.db per project<br/>tasks · metrics · hypotheses<br/>approvals · leads")]
        VAULT[("vault<br/>AES-256-GCM")]
        TRASH[("~/.trash<br/>7 days")]
    end

    WEB["Mission Control<br/>(spok1-web)"]

    GUARD -->|"ask / deny"| APR
    SCH -->|"agent-run jobs"| GOV
    WAT -->|"run-agent actions"| GOV
    GOV -->|"claude -p --max-turns"| PA
    APR --> NOT
    WAT --> NOT
    SCH --> NOT
    APR -->|"inject creds for one approved command"| VAULT
    LRN <--> LDB
    AUDR <--> LDB
    ORG <--> PDB
    CON <--> VAULT
    GUARD --> LDB
    WEB --> PDB
    WEB --> LDB
```

---

## 4. How a request runs

The manager's eight steps (PRD §2): route, recall, plan, pre-flight, dispatch, review, report, debrief.

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant D as Discord
    participant M as Manager
    participant Br as Brain + Mem0
    participant A as Auditor
    participant G as Governor
    participant S as Sub-agents
    participant Gd as Guard hook
    participant Ap as Approval broker
    participant RV as Reviewer
    participant L as Librarian

    You->>D: "In repo1, add a unit test for X and open a PR"
    D->>M: message (channel → project)
    M->>Br: 1. Route: load project registry + agents
    M->>Br: 2. Recall: top memories, beliefs, skills
    M->>M: 3. Plan: task graph + usage estimate + risk tier
    M->>A: 4. Pre-flight check
    A-->>M: revised plan + estimated saving
    alt over budget or any task ≥ T2
        M->>Ap: plan approval
        Ap->>D: 🔐 approval card
        You->>D: approve 61
        Ap-->>M: approved
    end
    M->>G: 5. Dispatch (≤3 in parallel, by priority)
    G->>S: run tasks (each coder in its own worktree)
    loop every tool call
        S->>Gd: tool call
        Gd-->>S: allow (T0) / ask / deny + reason
    end
    S-->>M: results
    M->>RV: 6. Review code changes
    RV-->>M: pass / changes requested
    M->>Ap: git push + open PR (T2)
    Ap->>D: approval card
    You->>D: approve
    M->>D: 7. Report: summary, links, usage spent
    M->>L: 8. Debrief: episode + outcome signals
    M->>A: usage review of the run
```

---

## 5. Guardrails and approvals

The guard runs before **every** tool call, on every machine. It is deterministic code with no LLM, so a prompt-injected agent can't argue its way past it.

```mermaid
flowchart TB
    Call["Tool call<br/>Bash · Write/Edit · MCP · Web"] --> Perm{"Claude Code<br/>permission settings<br/>(allow / ask / deny)"}
    Perm -->|deny| Blocked["⛔ Blocked<br/>(Forbidden list)"]
    Perm -->|allow / ask| Guard{{"guard hook classifies:<br/>shell parser · SQL type + WHERE<br/>cloud CLI verbs · HTTP method/host<br/>MCP tool tier · project path"}}

    Guard -->|"unclear → one tier higher<br/>unclassifiable → T2<br/>any deletion → T3"| Tier{Tier?}

    Tier -->|T0| Allow["✅ Run · logged"]
    Tier -->|T1| Batch["Queue for daily digest<br/>'approve all'"]
    Tier -->|T2| Grant{"Matching standing<br/>grant in grants.yaml?"}
    Tier -->|T3| T3Pre["Dry run shown<br/>+ verified backup / snapshot"]
    Tier -->|Forbidden| Blocked

    Grant -->|yes, in scope + not expired| Allow
    Grant -->|no| Card["🔐 Approval card<br/>Discord #approvals or terminal prompt"]
    T3Pre --> Card3["🔐 Approval card<br/>+ one-time confirm code"]

    Card -->|"approve N / ✅"| Inject["Broker injects write/admin creds<br/>for this one command, then removes them"]
    Card3 -->|"approve N CODE<br/>(a reaction never approves T3)"| Inject
    Card -->|"deny / no answer in 30 min"| Denied["❌ Denied · task waits"]
    Card3 -->|"deny / no answer"| Denied

    Inject --> Run["Run"]
    Allow --> Log
    Run --> Log[("Audit log<br/>append-only, hash-chained<br/>learning.db + daily JSONL → Blob")]
    Denied --> Log
    Blocked --> Log
```

| Tier | Examples | Approval |
|---|---|---|
| **T0** Auto | reads, worktree edits, tests, Mem0 writes, messages to me, learning changes | none, logged |
| **T1** Batch | agent-created reminders, local commits on `agent/*` | one tap in the daily digest |
| **T2** Approve | push/PR, installs, creating any resource, DB writes, messaging anyone else, new LLM jobs, going over budget | per action, or a standing grant |
| **T3** Two-key | any deletion, `DROP`, `rm -rf`, force push, mass actions, new outreach segment, cost > limit | approval + confirm code + backup + dry run; never a grant |
| **Forbidden** | skip-permissions, editing guardrails, setting `ANTHROPIC_API_KEY`, leaking secrets | blocked; I do it by hand |

---

## 6. The brain and sync between machines

Merge conflicts are avoided by design. **Only the VM edits curated files.** Every other writer only adds new, uniquely named files or appends to `feedback.md`.

```mermaid
flowchart LR
    subgraph Writers["Live sessions (any machine)"]
        LS["Laptop session"]
        VS["VM sessions / agents"]
    end

    subgraph BrainRepo["brain/ (git, private)"]
        direction TB
        subgraph AppendOnly["Add-only (anyone)"]
            INBOX["inbox/<machine>-<date>-<id>.md"]
            JOBS["jobs/*.yaml"]
            FB["feedback.md (append)"]
        end
        subgraph Curated["Curated (VM nightly/weekly jobs only)"]
            BEL["beliefs.yaml"]
            PROF["profile.md"]
            CM["CLAUDE.md"]
            AG["agents/"]
            SK["skills/ + meta.yaml"]
            PR["projects/"]
            EV["evals/"]
            CL["changelog.md"]
        end
        GR["guardrails.yaml · grants.yaml<br/>(only I edit, by hand)"]
    end

    Mem0[("Mem0 Cloud<br/>facts & episodes<br/>user_id + app_id=project")]

    LS -->|"SessionEnd: haiku extracts episode → commit + push"| INBOX
    LS --> JOBS
    LS --> FB
    VS --> INBOX
    LS <-->|"real time"| Mem0
    VS <-->|"real time"| Mem0

    Nightly["VM nightly / weekly learning jobs"] -->|"read"| AppendOnly
    Nightly -->|"eval-gated commits"| Curated

    Curated -->|"SessionStart: git pull --ff-only"| LS
    Curated -->|"agentd pulls every 2 min"| VS
```

How the brain is linked into Claude Code on every machine:

```mermaid
flowchart LR
    U["~/.claude/CLAUDE.md"] -->|"@import"| B["~/brain/CLAUDE.md"] -->|"@import"| P["~/brain/profile.md"]
    UA["~/.claude/agents"] -->|"symlink / junction"| BA["~/brain/agents"]
    US["~/.claude/skills"] -->|"symlink / junction"| BS["~/brain/skills"]
```

---

## 7. The learning engine

Learning changes the agents' **context** (beliefs, memories, skills, prompts, thresholds, routing), never model weights. Each change is driven by measured outcomes and has to pass the eval gate.

```mermaid
flowchart TB
    subgraph Signals["Signals captured (§5.1)"]
        EX["Explicit<br/>corrections · 👍/👎 · revert · noise · edits"]
        OUT["Outcome<br/>PR merged/closed · CI · final-vs-agent diff<br/>lead confirmed · reminder ack/snooze"]
        PROC["Process<br/>turns · denials · failures · rate limits"]
    end

    Signals --> EVT[("learning.db events")]
    EVT --> DEB["Reflexion debrief<br/>(episode per task)"]

    DEB --> CONS["Nightly consolidation<br/>episodes → facts → belief candidates"]
    CONS --> BEL["Belief model<br/>Beta(α, β) confidence"]
    BEL -->|"≥0.8 and ≥3 evidence"| PROF["profile.md"]
    BEL -->|"<0.6"| DEM["demoted"]
    BEL --> ACT["Active learning<br/>≤3 questions/day<br/>(uncertainty × impact)"]
    ACT -->|"my answers"| EX

    DEB --> SKL["Skill lifecycle<br/>candidate → trial → active<br/>→ rewritten → retired"]
    OUT --> DIFF["Diff learning<br/>style beliefs from my edits"]
    DIFF --> BEL
    DEB --> EVO["Weekly prompt evolution<br/>(GEPA-style)"]
    EX --> WT["Watcher tuning<br/>precision from ack / noise / do it"]
    PROC --> RT["Routing + model selection<br/>cheapest at equal success"]

    PROF --> GATE{"Eval gate<br/>golden tasks + rubric<br/>(sampled run)"}
    SKL --> GATE
    EVO --> GATE
    WT --> GATE
    RT --> GATE

    GATE -->|pass| COMMIT["git commit + changelog.md<br/>applied automatically"]
    GATE -->|fail| REJ["rejected + logged"]
    COMMIT --> MSG["Discord #learning<br/>'What I learned today' / weekly growth report"]
    MSG -.->|"revert <item>"| COMMIT

    RECALL["Scored retrieval at task start<br/>relevance × recency × importance, MMR"] -.-> Mem0[("Mem0")]
    CONS --> Mem0
```

| When | Runs |
|---|---|
| During a task | corrections → `feedback.md` + Mem0; scored retrieval |
| After each task | debrief, signal capture, skill stats |
| Nightly ~02:00 | consolidation, beliefs, profile, skills, diff learning → eval gate → auto-commit → report |
| Weekly | prompt evolution, watcher tuning, pruning, new eval tasks → growth report |

Learning can never create anything that costs usage or reaches outside the brain, and it can never touch the guardrails.

---

## 8. The auditor

```mermaid
flowchart LR
    Plan["Manager's plan"] --> Pre{"Pre-flight<br/>rules first;<br/>haiku if >20 units"}
    Pre -->|"cheaper model · reuse cache/skill<br/>less context · fewer agents · plain code"| Revised["Revised plan<br/>+ estimated saving"]
    Pre -->|"over budget"| Block["Block → approval (T2)"]
    Revised --> Run["Run"]
    Run --> Watch["During run:<br/>retry loops · re-reads ·<br/>stuck past ½ turn cap"]
    Watch -->|"stop / narrow"| Run
    Run --> Post["Post-run: usage per task<br/>→ learning.db"]
    Post --> Nightly["Nightly: top 5 usage sources<br/>→ optimisation proposals"]
    Nightly --> Gate{"Eval gate:<br/>no drop on must-pass,<br/>≤2% overall"}
    Gate -->|pass| Adopt["Adopt automatically"]
    Gate -->|fail| Drop["Reject"]
    Adopt --> Mon{"7-day live monitor:<br/>acceptance / correction rate"}
    Mon -->|"worse"| Rollback["Auto rollback + report"]
    Mon -->|"held"| Keep["Keep"]
    Post --> Weekly["Weekly usage report<br/>+ the auditor's own cost"]
```

Usage units per turn: **haiku 1 · sonnet 3 · opus 15**, calibrated from `--output-format json`. Budgets are per task, per project per day and per day overall, set in `guardrails.yaml`.

---

## 9. agentd: scheduler, watchers and notifier

```mermaid
flowchart LR
    subgraph In["Inputs"]
        JF["brain/jobs/*.yaml<br/>(pulled every 2 min)"]
        SRC["Sources: GitHub · IMAP/Gmail · HTTP<br/>logs · RSS · CRM sheet · claude auth status"]
    end

    JF --> SCH["Scheduler<br/>(SQLite job table)"]
    SCH -->|"reminder<br/>(no LLM)"| NOT
    SCH -->|"agent-run / digest"| GOV["Governor<br/>queue · ≤3 concurrent<br/>budgets · back-off"]
    GOV -->|"claude -p --max-turns"| Claude["claude binary<br/>(Max, setup-token)"]
    Claude --> NOT

    SRC --> W["Watcher"]
    W --> Rule{"Cheap rule<br/>check"}
    Rule -->|"conclusive"| Act
    Rule -->|"inconclusive"| Haiku["haiku classifier"] --> Act{"Action"}
    Act -->|ping| NOT
    Act -->|"ping+draft"| GOV
    Act -->|run-agent| GOV

    NOT["Notifier<br/>priority · quiet hours<br/>dedup · ≤20/h · alert IDs"]
    NOT -->|"P1: @mention, even in quiet hours"| Alerts["#alerts"]
    NOT -->|"P2: outside quiet hours"| Alerts
    NOT -->|"P3: batched"| Digest["#daily-digest"]

    Alerts --> Reply["ack · snooze · noise · do it<br/>✅ ❌ 🔕 🗑️"]
    Reply -->|"noise → precision"| Learn["learning engine"]
```

The example from PRD §3, the sales lead flow:

```mermaid
sequenceDiagram
    participant SA as Sales agent
    participant MB as Outreach mailbox
    participant LW as leads watcher (10 min)
    participant H as haiku classifier
    participant D as Discord #alerts
    actor You
    SA->>MB: send ≤30/day from approved templates (opt-out line)
    MB-->>LW: reply arrives
    LW->>H: classify
    H-->>LW: interested
    LW->>D: P1 ping: lead, quote, thread summary, draft reply
    You->>D: send 57
    D->>MB: draft sent (after confirmation)
    Note over LW: unsubscribe → suppression list
```

---

## 10. Project OS

Every project, from a one-agent side project to a 25-agent organisation, is the same kind of object. Here is the perfume pilot as an example:

```mermaid
flowchart TB
    YOU["👤 YOU (owner)<br/>approvals · direction"]
    MGR["MANAGER (chief of staff) + AUDITOR"]
    YOU --- MGR

    MGR --- MK["Marketing lead<br/>(account manager)"]
    MGR --- SL["Sales lead"]
    MGR --- DV["Development lead"]
    MGR --- RS["Research lead"]
    MGR --- LG["Legal lead"]
    MGR --- HR["HR lead"]

    MK --- MK1["content creator"] & MK2["graphic designer"] & MK3["video editor"] & MK4["content researcher"]
    SL --- SL1["outreach"] & SL2["lead-qual"] & SL3["CRM keeper"]
    DV --- DV1["web/store developer"] & DV2["QA tester"]
    RS --- RS1["market analyst"] & RS2["competitor analyst"]
    LG --- LG1["compliance checker"] & LG2["contract drafter"]
    HR --- HR1["JD writer"] & HR2["sourcer"] & HR3["onboarding docs"]
```

How work flows. Agents run only in **work cycles** or when an event wakes them. Only the manager and agentd run all the time.

```mermaid
flowchart LR
    subgraph Board["Task board (project.db: tasks)"]
        direction LR
        B1["backlog"] --> B2["ready"] --> B3["in-progress"] --> B4["review"] --> B5["awaiting-approval"] --> B6["done"]
        B3 -.-> BX["blocked"]
    end
    Cycle["Work cycle<br/>(schedule)"] --> Agent["Specialist"]
    Event["Event<br/>task assigned · approval · metric alert · lead"] --> Agent
    Agent -->|"picks up, works,<br/>attaches output"| Board
    Lead["Team lead"] -->|"creates + assigns"| Board
    Cross["Cross-team request"] -->|"via manager only"| Board
```

The team improvement loop, using marketing as the example:

```mermaid
flowchart LR
    R["Content researcher<br/>measures: own analytics<br/>+ benchmarks"] -->|"insight + evidence<br/>(hypothesis + metric)"| L["Account manager (lead)<br/>decides experiments"]
    L -->|"tasks"| S["Creator · designer · editor<br/>produce"]
    S --> Q["Brand guardian +<br/>legal check"]
    Q --> A{"Your approval<br/>(or standing grant)"}
    A -->|yes| P["Post via official API"]
    P --> R
    L -->|"worked? → team skill<br/>failed? → retired"| LE["Learning engine"]
```

Reports to you:

```mermaid
flowchart LR
    T["Each team"] --> EOD["End of day 19:00<br/>done · blocked · approvals waiting<br/>KPIs · usage · tomorrow"]
    T --> WK["Weekly review Mon 09:00<br/>KPIs vs target · per-agent scorecards<br/>hire / retire / retrain"]
    T --> MO["Monthly review<br/>goals · spend · start/stop/continue"]
    EOD --> DD["Discord ~10 lines + link"]
    WK --> DD
    EOD --> MCW["Mission Control archive"]
    WK --> MCW
    MO --> MCW
```

---

## 11. Multi-project isolation

Each project is a self-contained unit. Isolation is **enforced in code**: the guard, the vault and filtered Mem0 searches.

```mermaid
flowchart TB
    subgraph Shared["Shared by all projects"]
        GP["global profile + beliefs"]
        CA["core agents: manager · auditor · reviewer · librarian"]
        GS["global skills"]
        GRD["guardrails"]
    end

    subgraph PA["📁 Project A: perfume"]
        A1["brain/projects/perfume/<br/>project.md · org.yaml · agents · skills · kb"]
        A2["~/projects/perfume/<br/>workspace · repos · project.db"]
        A3["Mem0 app_id=perfume"]
        A4["vault entries tagged perfume"]
        A5["Discord category 📁 perfume"]
        A6["Blob container perfume/"]
    end

    subgraph PB["📁 Project B: tool"]
        B1["brain/projects/tool/..."]
        B2["~/projects/tool/..."]
        B3["Mem0 app_id=tool"]
        B4["vault entries tagged tool"]
        B5["Discord category 📁 tool"]
        B6["Blob container tool/"]
    end

    Shared --- PA
    Shared --- PB
    PA x--x|"guard denies paths · vault refuses tag mismatch<br/>Mem0 filtered by app_id"| PB
    PA -.->|"copy only via manager, T2, naming both projects"| PB

    REG[("brain/projects/registry.yaml<br/>id · type · status · priority<br/>usage_share · repos · Discord ID")]
    REG --- PA
    REG --- PB
```

Project lifecycle:

```mermaid
stateDiagram-v2
    [*] --> Active: new project (T2)
    Active --> Paused: pause (T1)
    Paused --> Active: resume (T1) + where-we-left-off summary
    Active --> Archived: archive (T2) snapshot → Blob, read-only
    Paused --> Archived: archive (T2)
    Archived --> Active: restore (T2)
    Archived --> Deleted: delete (T3) after export + verified backup
    Deleted --> [*]
```

---

## 12. Authentication and connectors

**The model login and the service logins are independent**, so scheduled jobs never depend on claude.ai connectors.

```mermaid
flowchart TB
    subgraph ModelAuth["Model login (Claude Max)"]
        LOGIN["/login<br/>(interactive manager only)<br/>channels · Remote Control · claude.ai connectors"]
        TOKEN["CLAUDE_CODE_OAUTH_TOKEN<br/>from setup-token (1 year)<br/>agentd + cron jobs only"]
        NOKEY["ANTHROPIC_API_KEY: never set"]
    end

    subgraph ServiceAuth["Service access (priority order)"]
        M1["1. Remote MCP added directly on VM<br/>claude mcp add … + claude mcp login<br/>Canva · Drive/Sheets · Notion · Slack"]
        M2["2. Local MCP / API client<br/>token from vault<br/>Meta · YouTube · Shopify · Gmail"]
        M3["3. Direct API from agentd<br/>(no LLM) insights, scheduled publish"]
        M4["4. claude.ai connectors<br/>interactive only"]
        M5["5. Custom channel into the logged-in manager<br/>(last resort)"]
    end

    Manager["Manager session"] --> LOGIN
    Jobs["agentd claude -p jobs<br/>ENABLE_CLAUDEAI_MCP_SERVERS=false"] --> TOKEN
    Manager --> M1 & M2 & M4
    Jobs --> M1 & M2
    Agentd["agentd"] --> M3
    Agentd -.-> M5 -.-> Manager

    Expiry["Login + token expiry watcher<br/>ping 10 days before /login expires"] -.-> LOGIN
    Expiry -.-> M2
```

Connecting an account:

```mermaid
sequenceDiagram
    actor You
    participant M as Manager
    participant Svc as Service (OAuth)
    participant V as Vault
    participant Reg as connectors/registry.yaml
    You->>M: /connect instagram
    M-->>You: prerequisites + official sign-in link
    You->>Svc: sign in yourself (never a password to agents)
    Svc-->>V: token stored, project-tagged, encrypted
    M->>Svc: read-only connection test
    M-->>You: proposed agent → tool → tier mapping (T2)
    You->>M: approve
    M->>Reg: write mapping (guard enforces it)
```

---

## 13. Discord layout and routing

```mermaid
flowchart TB
    subgraph Server["🔒 Private server (only me + the bot)"]
        subgraph HQ["📌 HQ"]
            CMD["#command"]
            APP["#approvals"]
            ALR["#alerts (notifications ON)"]
            DIG["#daily-digest (muted)"]
            LRN["#learning"]
        end
        subgraph P1["📁 perfume"]
            GEN["#general"]
            TEAMS["#marketing · #sales · #dev · #research …"]
            THR["threads per big task / approval"]
        end
        ARC["🗄 Archive (read-only categories)"]
    end

    You["👤 Me (only allowed user ID)"] --> CMD & GEN & TEAMS & APP
    CMD -->|"active project or #perfume prefix"| MGR["VM manager<br/>(the only gateway connection)"]
    GEN -->|"channel decides project"| MGR
    TEAMS -->|"→ that team's lead"| MGR

    AGD["agentd (bot REST)"] -->|"cards, channels, threads, reactions"| APP & ALR & DIG & LRN
    LAP["Laptops (webhooks, send-only)"] --> ALR & DIG
```

---

## 14. Code packages

The repo is a uv workspace (`packages/spok1-*`, each with a `src/` layout and its own `tests/`). `spok1-core` is the shared library. **`spok1-guard` depends on nothing, not even core**: it runs on every tool call, so it uses only the standard library and starts fast. No package depends on `spok1-cli` or `spok1-web`.

```mermaid
flowchart BT
    CORE["spok1-core<br/>config · schemas · db · audit_log · vault"]
    GUARD["spok1-guard<br/>PreToolUse classifier<br/>(stdlib only, no deps)"]
    AGENTD["spok1-agentd<br/>scheduler · watchers · notifier · governor<br/>approvals · auditor · org · connectors · learning"]
    MCP["spok1-mcp<br/>notify · custom channel · local connector servers"]
    WEB["spok1-web<br/>Mission Control"]
    CLI["spok1-cli<br/>the spok1 command"]

    AGENTD --> CORE
    MCP --> CORE
    WEB --> CORE
    CLI --> CORE
    AGENTD -->|"tier checks for its own actions"| GUARD
    CLI -->|"local control API"| AGENTD

    HOOKS["claude/hooks/*.sh, *.ps1"] -->|"exec"| GUARD
    SETTINGS["claude/settings.*.json"] -.-> HOOKS
```

| Package | Runs on | Started by |
|---|---|---|
| `spok1-core` | everywhere | (library) |
| `spok1-guard` | every machine | the PreToolUse hook, on every tool call |
| `spok1-agentd` | VM only | systemd `agentd.service` |
| `spok1-mcp` | VM + laptops (`notify`), VM (channel, connectors) | Claude Code, as MCP servers |
| `spok1-web` | VM only | agentd / systemd, bound to the Tailscale interface |
| `spok1-cli` | everywhere | me |
