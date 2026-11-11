# AI Talent Screener & Technical Interviewer

## Phase 7: Frontend Clean Architecture, State Synchronization & UX Flows (Next.js)

* **Document ID:** `ARCH-SPEC-PHASE-07`

* **Architectural Paradigm:** Frontend Clean Architecture, Hexagonal Ports & Adapters, Deterministic Finite State Machines (FSM), Reactive Stream Coalescing

* **Implementation Policy:** Strict Zero-Code Conceptual & Mathematical Specification

* **Target Subsystem:** Next.js (App Router) Enterprise Web Application (*Candidate Interview Portal* & *Recruiter / Hiring Manager Analytics Workspace*)

## 1. Frontend Architectural Layers & Clean Architecture Governance

### 1.1 Applying the Clean Architecture Dependency Rule to Next.js App Router

Enterprise Next.js applications frequently suffer from severe architectural degradation when network fetching (`fetch`, WebSocket event listeners), browser hardware APIs (`MediaDevices`, `AudioContext`, `ClipboardEvent`), and domain business rules (score normalization, interview turn validation, comparison matrix ranking) are entangled directly inside React UI components or custom hooks.

To enforce Robert C. Martin's **Clean Architecture** and the **Inward Dependency Rule** on the frontend, the `frontend/` workspace is partitioned into four concentric architectural layers per feature module (`recruitment`, `batch-ingestion`, `live-interview`, `evaluation-matrix`):

| Concentric Frontend Layer (Inner $\rightarrow$ Outer) | Architectural Responsibility | Permitted Dependencies | Strictly Prohibited Awareness | 
| ----- | ----- | ----- | ----- | 
| **1. Frontend Domain Layer (`domain/`)** | Pure client-side business entities, immutable Value Objects, deterministic UI state machines, comparison matrix normalization math, and kinematic anomaly calculators. | Host language standard runtime primitives only (pure mathematical functions, immutable data structures). | React (`useState`, `useEffect`), Next.js (`next/navigation`, Server Actions), DOM/Window APIs (`window`, `document`, `AudioContext`), WebSocket/HTTP clients, CSS/Tailwind. | 
| **2. Frontend Application Layer (`application/`)** | Client Use Case Interactors, Inbound UI Driving Ports, Outbound Driven Port interfaces (for REST, WebSockets, Web Audio, Browser Telemetry), and framework-agnostic Reactive Store contracts. | **Frontend Domain Layer** only. | Concrete browser `WebSocket` instances, `fetch` / Axios headers, `navigator.mediaDevices` streams, JSX/HTML markup, Tailwind classes. | 
| **3. Frontend Infrastructure Layer (`infrastructure/`)** | Concrete Adapters implementing Outbound Driven Ports: Multiplexed WebSocket Client Adapter, Web Audio API Ring-Buffer Adapter, DOM Integrity Sensor Adapter, SSE Stream Adapter, and REST Anti-Corruption Layer (ACL) Mappers. | **Frontend Application Layer** (Port contracts & DTOs), **Frontend Domain Layer**, and Browser/Vendor Web APIs. | React View Components, Next.js page route layouts, visual styling logic. | 
| **4. Presentation & Delivery Layer (`presentation/` & `app/`)** | Next.js App Router Server Components (RSC) for initial data hydration, Client Components (`"use client"`) for interactive views, Thin Controller Hooks binding Application Stores to Pure Presentational Components, and Design System primitives. | **Frontend Application Layer** (invokes Use Cases and subscribes to View-Model projections) and **Domain Value Objects** (for read-only display). | Direct instantiation of `WebSocket`, `EventSource`, `AudioContext`, or raw `fetch` calls; business or scoring calculations inside JSX templates. | 

### 1.2 Architecture Decision Record: ADR-010 (Next.js App Router Clean Architecture & RSC Boundary Governance)

| ADR Field | Specification Details | 
| ----- | ----- | 
| **Identifier** | **ADR-010: Hexagonal Frontend State Isolation & Next.js Server/Client Component Boundary Governance** | 
| **Status** | Accepted & Enforced | 
| **Context** | Next.js App Router introduces React Server Components (RSC) alongside Client Components. Without strict boundaries, teams either mark the entire application tree `"use client"` (sacrificing server-side pre-rendering, initial bundle size, and secure token handling) or scatter server fetches and client state hooks haphazardly, making it impossible to unit-test complex real-time workflows like the WebSocket voice interview or the 500-CV SSE progress monitor without a full browser DOM environment. | 
| **Decision** | 1\. **RSC as Thin Server-Side Delivery Shells:** Next.js `app/` route segments (`page`, `layout`, `loading`, `error`) operate strictly as Server Component shells responsible for authenticating the session cookie, executing initial read-only query hydration via Server-Side Application Query Ports, and passing serialized initial snapshots into isolated Client Island boundaries.  2\. **Framework-Agnostic Application Store & FSM Engine:** All high-frequency interactive state (the 13-state Candidate Interview FSM and the 250ms-coalesced Batch Telemetry Store) resides in the **Frontend Application Layer** behind observable state ports. React components subscribe to narrow, memoized selector slices via thin adapter hooks (`useInterviewStateSelector`, `useBatchTelemetrySlice`), ensuring that $50\text{ Hz}$ audio energy updates re-render only the waveform canvas and never trigger reconciliation of the transcript tree.  3\. **Client-Side Composition Root Provider:** Concrete browser adapters (`BrowserWebAudioAdapter`, `MultiplexedWebSocketAdapter`, `DomIntegritySensorAdapter`) are instantiated inside a top-level Dependency Injection Provider at the Client Island boundary and injected into Application Use Cases via constructor/factory binding. | 
| **Alternatives Considered** | 1\. *Direct `useEffect` + `WebSocket` / `AudioContext` inside React Components:* Rejected because React StrictMode double-invocation, component re-renders, and route transitions cause duplicate socket handshakes, audio buffer memory leaks, and untestable race conditions.  2\. *Redux Toolkit / Monolithic Global Store across the Entire App:* Rejected because Recruiter Dashboard state and Candidate Portal real-time audio state have zero overlap and belong to isolated Bounded Contexts. | 
| **Consequences** | **Positive:** 100% of interview state transitions, barge-in synchronization rules, and candidate comparison matrix calculations can be unit-tested deterministically in under $50\text{ ms}$ without a browser or DOM; zero audio glitches during React UI re-renders.  **Trade-offs:** Requires explicit ACL mapping between backend transport DTOs and frontend Domain View-Models. | 

### 1.3 Visual Architecture Diagram: Frontend Clean Architecture & Reactive Data Flow

```
flowchart TD
    subgraph AppRouter["4A. Next.js App Router Shell (Server Components & Edge Middleware)"]
        MW["Edge Auth & Tenant Routing Middleware"]
        RSC_Rec["Recruiter Workspace RSC Shell\n(SSR Initial Hydration)"]
        RSC_Cand["Candidate Portal RSC Shell\n(Magic-Link Pre-Validation)"]
    end

    subgraph Presentation["4B. Presentation Layer (Client Islands & Pure UI Views)"]
        Hook_Bind["Thin Adapter Hooks\n(useInterviewController, useComparisonMatrix)"]
        UI_Interview["Candidate Interview Views\n(WaveformCanvas, TranscriptStream, TurnControls)"]
        UI_Dashboard["Recruiter Dashboard Views\n(BatchMonitorGrid, ComparisonMatrixTable, RubricRadar)"]
    end

    subgraph Application["2. Frontend Application Layer (Use Cases & Driven Ports)"]
        subgraph ClientUseCases["Inbound Client Use Cases (Interactors)"]
            UC_StartInt["InitializeInterviewSessionUseCase"]
            UC_Turn["ManageConversationalTurnUseCase"]
            UC_BargeIn["ExecuteClientBargeInUseCase"]
            UC_BatchMon["SubscribeBatchProgressUseCase"]
            UC_Compare["ComputeCandidateComparisonUseCase"]
        end
        subgraph DrivenPorts["Outbound Driven Ports (Abstract Contracts)"]
            P_WS["InterviewRealtimeGatewayPort"]
            P_Audio["AudioMediaPipelinePort"]
            P_Sensor["IntegrityTelemetrySensorPort"]
            P_SSE["BatchProgressStreamPort"]
            P_REST["RecruitmentRestApiPort"]
        end
    end

    subgraph Domain["1. Frontend Domain Layer (Pure Models, FSM & Normalization Math)"]
        FSM["InterviewPortalStateMachine\n(13 Deterministic States)"]
        KinMath["IntegrityKinematicCalculator\n(Paste Velocity & Focus Entropy)"]
        MatMath["ComparisonMatrixNormalizer\n(Z-Score & Rubric Delta Algebra)"]
        VO["Immutable Value Objects\n(TurnTranscriptItem, CandidateMatrixRow)"]
    end

    subgraph Infrastructure["3. Frontend Infrastructure Layer (Browser & Network Adapters)"]
        Ad_WS["MultiplexedWebSocketAdapter\n(Binary + JSON Framing, Auto-Resume)"]
        Ad_Audio["WebAudioWorkletPipelineAdapter\n(Opus Encoder, VAD, RingBuffer)"]
        Ad_Sensor["DomIntegritySensorAdapter\n(Visibility, Blur, Clipboard Listeners)"]
        Ad_SSE["EventSourceSSEAdapter\n(Last-Event-ID Reconnection)"]
        Ad_REST["FetchRestApiACLAdapter\n(RFC 9457 Error Translator)"]
    end

    %% Inward Dependency Connections
    MW --> RSC_Rec & RSC_Cand
    RSC_Rec --> UI_Dashboard
    RSC_Cand --> UI_Interview
    UI_Interview & UI_Dashboard -->|"Dispatches Commands / Selects Slices"| Hook_Bind
    Hook_Bind -->|"Invokes"| ClientUseCases

    ClientUseCases -->|"Transitions & Validates"| Domain
    ClientUseCases -->|"Calls Abstract Interface"| DrivenPorts

    Ad_WS ==>|"Implements (DIP)"| P_WS
    Ad_Audio ==>|"Implements (DIP)"| P_Audio
    Ad_Sensor ==>|"Implements (DIP)"| P_Sensor
    Ad_SSE ==>|"Implements (DIP)"| P_SSE
    Ad_REST ==>|"Implements (DIP)"| P_REST

```

### 1.4 Frontend Outbound Driven Port Specifications

| Port Interface Name | Conceptual Method Signatures | Input Preconditions | Output Postconditions & Observables | Failure Modes & Client Recovery | 
| ----- | ----- | ----- | ----- | ----- | 
| **`InterviewRealtimeGatewayPort`** | 1\. `Connect(SessionID, WSTicketJWT, LastServerSeq) -> ConnectionStateObservable`  2\. `SendAudioFrame(TurnIndex, ChunkSeq,PcmOrOpusBytes)`  3\. `SendControlEvent(ClientEventEnvelope)`  4\. `ObserveServerEvents() -> ServerEventStream` | Valid short-lived `WSTicketJWT` acquired from handshake endpoint; monotonic `ChunkSeq`. | Emits strongly typed Domain Events mapped from Phase 5 WebSocket frames; maintains automatic heartbeat ping every $10\text{ s}$. | `SocketTransportDisconnectedError` $\rightarrow$ Triggers FSM transition to `RECONNECTING_GRACE` with exponential backoff ($0.5\text{ s}, 1\text{ s}, 2\text{ s}, 4\text{ s}$) and replays missed frames via `LastServerSeq`. | 
| **`AudioMediaPipelinePort`** | 1\. `AcquireMicrophoneStream(AcousticConstraints) -> HardwareDiagnosticReceipt`  2\. `StartCaptureAndVAD(OnVoiceFrame, OnLocalBargeIn) -> Void`  3\. `EnqueueTTSPlaybackFrame(AudioSeq, FrameBytes)`  4\. `FlushPlaybackBufferImmediately() -> TruncatedPlaybackMs` | User granted microphone permission; `AudioContext` resumed via explicit user gesture; echo cancellation (`echoCancellation: true`, `noiseSuppression: true`) enabled. | Captures $20\text{ ms}$ audio frames off the main UI thread via `AudioWorklet`; executes local acoustic VAD; plays TTS audio gaplessly via ring buffer. | `MicrophonePermissionDeniedError`, `HardwareSampleRateUnsupportedError`, `AudioDeviceUnpluggedError` $\rightarrow$ Pauses turn timer and prompts candidate to select fallback input device or switch to text-supplement mode. | 
| **`IntegrityTelemetrySensorPort`** | 1\. `AttachNonIntrusiveSensors(SessionScope) -> TelemetryAnomalyStream`  2\. `RecordKeystrokeCadenceSample(InterKeyIntervalMs)`  3\. `DetachAllSensors() -> FinalSessionTelemetryDigest` | Candidate has accepted the transparent pre-interview integrity disclosure; active only during `IN_PROGRESS` interview states. | Emits privacy-preserving kinematic and focus-loss anomaly vectors (`CLIENT_INTEGRITY_TELEMETRY`) without capturing raw clipboard text outside the input box or recording screen pixels. | `SensorAttachmentBlockedByExtensionError` $\rightarrow$ Logs browser environment metadata flag (`TELEMETRY_DEGRADED`) in session handshake. | 
| **`BatchProgressStreamPort`** | `SubscribeBatchTelemetry(BatchID, LastEventID) -> CoalescedBatchProgressObservable` | Authenticated Recruiter session; `BatchID` in non-terminal state. | Streams $250\text{ ms}$-windowed `BatchProgressSnapshot` domain models; buffers incoming frames via `requestAnimationFrame` for $60\text{ FPS}$ UI stability. | `SSEStreamInterruptedError` $\rightarrow$ Automatically reconnects with `Last-Event-ID` header; falls back to $3\text{-second}$ conditional ETag polling if corporate proxy strips `text/event-stream`. | 

## 2. Candidate Interview Portal Architecture

### 2.1 Deterministic Finite State Machine (FSM) for the Live Interview Portal

To eliminate race conditions between microphone capture, AI voice playback, candidate barge-in interruptions, and network reconnections, the Candidate Interview Portal is governed by a formal, deterministic 13-state Finite State Machine (`InterviewPortalStateMachine`) residing in the **Frontend Domain Layer**.

```
stateDiagram-v2
    [*] --> BOOTSTRAP_HANDSHAKE : Magic Link Opened
    BOOTSTRAP_HANDSHAKE --> HARDWARE_PREFLIGHT : Handshake 200 OK (WSTicket Issued)
    BOOTSTRAP_HANDSHAKE --> TERMINATED_ERROR : Token Expired / Invalid (401/403)

    HARDWARE_PREFLIGHT --> CONNECTING_SOCKET : Mic Verified + AudioWorklet Ready + Consent Signed
    CONNECTING_SOCKET --> READY_ICEBREAKER : SERVER_SESSION_READY Received
    CONNECTING_SOCKET --> RECONNECTING_GRACE : Handshake Timeout

    READY_ICEBREAKER --> AI_SPEAKING : First SERVER_TTS_AUDIO_CHUNK / QUESTION_DELTA
    
    state ActiveConversationalLoop {
        AI_SPEAKING --> CANDIDATE_LISTENING_VAD : SERVER_TURN_READY_FOR_INPUT (TTS Playback Finished)
        AI_SPEAKING --> BARGE_IN_INTERRUPTING : Local VAD > 280ms Voiced Energy Detected
        
        BARGE_IN_INTERRUPTING --> CANDIDATE_LISTENING_VAD : SERVER_TTS_FLUSH_INTERRUPT Acked + RingBuffer Cleared
        
        CANDIDATE_LISTENING_VAD --> CANDIDATE_THINKING_PAUSE : Silence > 450ms (Syntactic Clause Incomplete)
        CANDIDATE_THINKING_PAUSE --> CANDIDATE_LISTENING_VAD : Candidate Resumes Speaking Before EOT Threshold
        
        CANDIDATE_LISTENING_VAD --> COMMITTING_TURN : Manual "Done Speaking" Click OR Silence >= Adaptive EOT
        CANDIDATE_THINKING_PAUSE --> COMMITTING_TURN : Silence >= Adaptive EOT Threshold
        
        COMMITTING_TURN --> EVALUATING_COGNITIVE : SERVER_STT_COMMITTED Received
        EVALUATING_COGNITIVE --> AI_SPEAKING : Next Turn SERVER_QUESTION_TEXT_DELTA / TTS Chunk
    }

    AI_SPEAKING --> RECONNECTING_GRACE : WebSocket Close / Heartbeat Timeout
    CANDIDATE_LISTENING_VAD --> RECONNECTING_GRACE : WebSocket Close / Heartbeat Timeout
    EVALUATING_COGNITIVE --> RECONNECTING_GRACE : WebSocket Close / Heartbeat Timeout

    RECONNECTING_GRACE --> AI_SPEAKING : SERVER_SESSION_RESUMED (Restored to Speaking)
    RECONNECTING_GRACE --> CANDIDATE_LISTENING_VAD : SERVER_SESSION_RESUMED (Restored to Input)
    RECONNECTING_GRACE --> TERMINATED_ERROR : Grace Period Exhausted (> 180s)

    EVALUATING_COGNITIVE --> COMPLETED_SUMMARY : SERVER_INTERVIEW_COMPLETED Received
    AI_SPEAKING --> COMPLETED_SUMMARY : Closing Message Playback Finished
    COMPLETED_SUMMARY --> [*]
    TERMINATED_ERROR --> [*]

```

### 2.2 Candidate Portal State Invariants & UI Behavior Matrix

| FSM State Identifier | Microphone Capture & Outbound Audio | Web Audio TTS Playback Buffer | Permitted Candidate UI Actions | Primary Visual Feedback Indicator | 
| ----- | ----- | ----- | ----- | ----- | 
| `BOOTSTRAP_HANDSHAKE` | Inactive (`OFF`). | Uninitialized. | View role overview and privacy disclosure. | Skeleton loader with cryptographic session verification badge. | 
| `HARDWARE_PREFLIGHT` | Local loop-back test only (Zero bytes sent to server). | Plays $2\text{-second}$ calibration chime to verify speaker output. | Select microphone/speaker devices; test vocal level; click *"Start Technical Interview"*. | Interactive decibel meter (`-60 dBFS` to `0 dBFS`) and echo-cancellation check. | 
| `AI_SPEAKING` | Active in **Barge-In Monitor Mode** (Acoustic echo cancellation active; streams VAD energy). | Actively draining ring buffer to `AudioDestinationNode`. | Listen to interviewer; speak for $\ge 280\text{ ms}$ to barge-in; or click *"Interrupt & Answer"*. | Dynamic AI voice orb pulsing with TTS playback RMS envelope; live streaming question text. | 
| `BARGE_IN_INTERRUPTING` | Actively streaming candidate speech frames (`CLIENT_AUDIO_CHUNK`). | **Immediate Hardware Gain Ramp-Down** ($15\text{ ms}$ cosine fade-out to prevent speaker click) + Buffer Flush. | Continue speaking uninterrupted. | Instant visual transition to *"Listening to your interruption..."* indicator. | 
| `CANDIDATE_LISTENING_VAD` | Actively streaming $20\text{ ms}$ Opus frames + real-time captions (`SERVER_STT_PARTIAL`). | Empty / Muted. | Speak explanation; type/paste supplemental code or complexity notation; click *"Done Speaking"*. | Candidate waveform active; live streaming partial transcript; countdown turn timer. | 
| `CANDIDATE_THINKING_PAUSE` | Streaming silence frames; server pre-warming checkpoint. | Empty / Muted. | Resume speaking seamlessly or click *"Submit Answer Now"*. | Subtle amber *"Holding the floor while you think..."* progress arc showing adaptive EOT window. | 
| `EVALUATING_COGNITIVE` | Muted for turn transition (`TurnIndex` locked). | Pre-buffering incoming TTS frames for Turn $n+1$. | Read committed transcript; await next question ($P_{50} \le 1.1\text{ s}$). | Step indicator reflecting `SERVER_COGNITIVE_STATE` (*"Analyzing architectural depth..."*). | 
| `RECONNECTING_GRACE` | Buffered locally in circular memory ($30\text{ s}$ capacity). | Paused at exact sample offset. | Wait for automatic reconnection or click *"Retry Connection"*. | Non-alarming banner: *"Network hiccup detected—your turn progress is safely checkpointed."* | 

### 2.3 Web Audio API Ring Buffer & Barge-In Synchronization Mathematics

To guarantee glitch-free voice playback and instantaneous barge-in truncation off the main browser thread, the `WebAudioWorkletPipelineAdapter` manages a lock-free **SharedArrayBuffer Circular Ring Buffer** operating at sample rate $f_s = 48,000\text{ Hz}$:

1. **Adaptive Jitter Cushion Sizing (**$B_{\text{cushion}}$**):** Let $\sigma_{\text{net}}$ denote the exponentially weighted moving standard deviation of inter-arrival times for `SERVER_TTS_AUDIO_CHUNK` frames over the last $20$ frames. Playback begins only when buffered audio duration $T_{\text{buf}}$ reaches the adaptive cushion threshold:

$$
T_{\text{buf}} \ge B_{\text{cushion}} = \text{clamp}\left( 40\text{ ms} + 2.5 \cdot \sigma_{\text{net}}, \, 40\text{ ms}, \, 160\text{ ms} \right)
$$

2. **Exact Barge-In Playback Offset Calculation (**$t_{\text{heard}}$**):** When the candidate barges in at client monotonic timestamp $t_{\text{barge}}$, the server may have already synthesized and transmitted $T_{\text{sent}}$ milliseconds of TTS audio across the network, of which a portion is still sitting unplayed inside the client's ring buffer. To ensure the Phase 4 LangGraph transcript ledger records **only the words the candidate actually heard** before interrupting, the client computes exact heard duration $t_{\text{heard}}$ (in milliseconds) from the `AudioContext.currentTime` hardware clock:

$$
t_{\text{heard}} = \max\left( 0, \, T_{\text{enqueued}} - \frac{N_{\text{unread\_samples}}}{f_s} \times 1000 \right)
$$

This exact $t_{\text{heard}}$ value is transmitted in `CLIENT_BARGE_IN_SIGNAL.InterruptedAudioPlaybackMs`, allowing the backend `LangGraphInterviewOrchestratorACL` to slice the assistant's question string at the corresponding word-timestamp boundary.

### 2.4 Client-Side Integrity & Anti-Cheating Telemetry Pipeline

A critical requirement of the platform is detecting candidates who read answers generated by external LLMs on a second monitor, switch tabs to search documentation, or paste pre-written blocks into the supplemental text input—**without** requiring invasive spyware, screen-sharing permissions, or raw keystroke logging that violates enterprise GDPR/privacy standards.

The `DomIntegritySensorAdapter` and `IntegrityKinematicCalculator` (Domain Layer) compute four non-intrusive, mathematical telemetry signals per turn $t$:

#### 1. Focus-Loss & Multi-Monitor Gaze Shift Entropy ($S_{\text{focus}, t}$)

The sensor listens to `document.visibilitychange`, `window.onblur`/`onfocus`, and cursor boundary exits (`mouseleave` toward secondary display edges immediately following `SERVER_QUESTION_TEXT_DELTA`). Let $\{ \Delta \tau_{\text{blur}, 1}, \dots, \Delta \tau_{\text{blur}, m} \}$ be the durations of focus-loss events during turn $t$ of total duration $T_{\text{turn}, t}$. The **Focus Anomaly Score** $S_{\text{focus}, t} \in [0, 1]$ is:

$$
S_{\text{focus}, t} = 1 - \exp\left( -\eta_1 \cdot \frac{\sum_{j=1}^{m} \Delta \tau_{\text{blur}, j}}{T_{\text{turn}, t}} - \eta_2 \cdot \max\left(0, m - 1\right) \right)
$$

Where $\eta_1 = 3.5$ and $\eta_2 = 0.40$. A single brief accidental click outside the window ($m=1, \Delta \tau < 1\text{ s}$) produces a negligible score ($S_{\text{focus}, t} < 0.08$), whereas repeated tab switches or sustained secondary-window focus during question delivery drives $S_{\text{focus}, t} \rightarrow 1.0$.

#### 2. Clipboard Paste Velocity vs. Keystroke Kinematic Divergence ($S_{\text{paste}, t}$)

When a candidate uses the supplemental text box (for architectural equations, SQL/API schemas, or complexity explanations), the sensor measures character insertion bursts. For any insertion event $e_i$ adding $\Delta L_i$ characters within $\Delta t_i$ milliseconds, the instantaneous insertion velocity is $v_i = \frac{\Delta L_i}{\max(\Delta t_i, 1)}$. Combined with the coefficient of variation of inter-keystroke intervals $\text{CV}_{\text{IKI}} = \frac{\sigma_{\text{IKI}}}{\mu_{\text{IKI}}}$ (human typing exhibits natural variance $\text{CV}_{\text{IKI}} \in [0.35, 0.85]$, whereas programmatic auto-typers exhibit near-zero variance $\text{CV}_{\text{IKI}} < 0.08$):

$$
S_{\text{paste}, t} = \max_{i} \left( \sigma\left( \frac{\Delta L_i - L_{\text{threshold}}}{25} \right) \cdot \mathbb{I}\left( v_i > 15\text{ chars/sec} \right) \right) + 0.5 \cdot \mathbb{I}\left( \text{CV}_{\text{IKI}} < 0.10 \land L_{\text{total}} > 60 \right)
$$

Where $L_{\text{threshold}} = 40\text{ characters}$ (allowing harmless pasting of a single variable name or short token while flagging multi-line external LLM paste bursts).

#### 3. Vocal Initiation Lag vs. Post-Lag Speech Fluency Anomaly ($S_{\text{cadence}, t}$)

Candidates using a real-time "AI Interview Copilot" transcription overlay typically exhibit a characteristic **Two-Phase Acoustic Cadence Signature**:

* **Phase A (External Copilot Generation Lag):** An abnormally long initial silence gap $\Delta t_{\text{init}}$ ($6\text{--}14\text{ seconds}$) after the interviewer finishes a Socratic follow-up probe, often accompanied by zero filler vocalizations while waiting for the external screen to stream text.

* **Phase B (Teleprompter Reading Cadence):** Immediately following $\Delta t_{\text{init}}$, the candidate speaks at a high, uniform Words-Per-Minute rate ($\text{WPM}_t > 165$) with abnormally low pause-to-speech entropy ($\sigma_{\text{pause}} \rightarrow 0$) and elevated lexical density (matching written prose rather than spontaneous spoken engineering discourse).

Let $Z_{\text{lag}, t} = \frac{\Delta t_{\text{init}, t} - \mu_{\text{lag}, c}}{\sigma_{\text{lag}, c}}$ be the candidate-calibrated Z-score of initial response latency, and let $U_{\text{uniform}, t} = \frac{\text{WPM}_t}{1 + \text{PauseEntropy}_t}$ measure teleprompter reading uniformity. The **Cadence Anomaly Signal** $S_{\text{cadence}, t} \in [0, 1]$ is:

$$
S_{\text{cadence}, t} = \sigma\left( \kappa_1 \cdot \max\left(0, Z_{\text{lag}, t} - 1.5\right) \cdot \max\left(0, \frac{U_{\text{uniform}, t} - U_{\text{baseline}}}{U_{\text{baseline}}}\right) - \kappa_0 \right)
$$

#### 4. Composite Client Telemetry Payload (Input to Phase 4 `EvaluationNode`)

At the conclusion of every turn (or immediately upon a high-severity spike), the client transmits `CLIENT_INTEGRITY_TELEMETRY` containing $(S_{\text{focus}, t}, S_{\text{paste}, t}, S_{\text{cadence}, t})$. In Phase 4, when the composite client telemetry score exceeds $\theta_{\text{probe}} = 0.65$, the LangGraph conditional router immediately routes the next turn to the `SocraticFollowUpNode` with a context-specific *"Why did you choose X over Y in your specific implementation?"* probe that generic off-screen LLM copilot prompts cannot answer without deep project memory.

## 3. Recruiter Dashboard & Side-by-Side Candidate Comparison Matrix Architecture

### 3.1 Architecture Decision Record: ADR-011 (Real-Time Batch Telemetry Transport & Frame Coalescing)

| ADR Field | Specification Details | 
| ----- | ----- | 
| **Identifier** | **ADR-011: Server-Sent Events (SSE) with Last-Event-ID Resumption & RequestAnimationFrame UI Buffer for Batch Monitoring** | 
| **Status** | Accepted & Enforced | 
| **Context** | When a recruiter uploads a batch of $500$ CVs, documents transition rapidly across $6$ pipeline stages ($\approx 3,000$ state transitions in under $180\text{ seconds}$). Using short-interval HTTP polling ($1\text{ s}$) across $50$ concurrent recruiters generates $3,000\text{ req/min}$ of redundant database reads and suffers from stale latency. Conversely, using raw unbuffered WebSockets for one-way progress monitoring bypasses standard corporate HTTP/2 proxies and causes React UI thread lockups ("jank") if every document event triggers an immediate state re-render. | 
| **Decision** | 1\. **Primary Transport—HTTP/2 Server-Sent Events (SSE):** Use `GET /api/v1/batches/{batch_id}/events` (`text/event-stream`). SSE operates natively over multiplexed HTTP/2 connections, traverses enterprise firewalls seamlessly, and supports built-in browser reconnection via the standard `Last-Event-ID` header.  2\. **Dual-Stage Frame Coalescing (250ms Server + 16.6ms Client RAF):** The backend SSE publisher coalesces worker stage transitions into $250\text{ ms}$ tumbling snapshots (`ADR-006`). On the frontend, `EventSourceSSEAdapter` writes incoming snapshots into a mutable staging reference and schedules a single store commit inside `window.requestAnimationFrame` ($16.6\text{ ms}$ frame boundary), guaranteeing smooth $60\text{ FPS}$ progress animations and virtualized table updates.  3\. **Conditional ETag Polling Fallback:** If an enterprise proxy blocks streaming responses ($> 5\text{ s}$ without heartbeat), `EventSourceSSEAdapter` transparently degrades to conditional `GET /batches/{id}/status` polling using `If-None-Match` ETag headers ($304\text{ Not Modified}$ zero-body responses) at a $2,500\text{ ms}$ interval. | 
| **Alternatives Considered** | 1\. *Fixed 1-Second REST Polling Only:* Rejected due to high database/API overhead and poor real-time UX.  2\. *Full Bi-Directional WebSocket for Batch Dashboard:* Rejected as unnecessary protocol overhead for strictly unidirectional server-to-client telemetry. | 
| **Consequences** | **Positive:** Zero UI thread blocking during 500-CV high-velocity ingestion; automatic gapless state recovery on browser tab refresh; enterprise firewall compatibility.  **Trade-offs:** Requires implementing the ETag fallback strategy inside `EventSourceSSEAdapter`. | 

### 3.2 Side-by-Side Candidate Comparison Matrix: Mathematical Normalization & Ranking Model

When an Engineering Manager or Recruiter selects $M \in \{2, 3, 4\}$ shortlisted finalists for a Job Requisition $J$ to compare in the **Side-by-Side Candidate Comparison Matrix**, raw unnormalized scores can be misleading: one candidate may have had a slightly higher CV keyword score but failed Socratic probing during the live interview, while another had a modest resume layout but demonstrated Level $4.8/5.0$ Principal-grade mastery under deep Socratic questioning.

The **Domain Layer** (`ComparisonMatrixDomainService` on the backend and `ComparisonMatrixNormalizer` on the frontend) synthesizes a unified, multi-dimensional comparative model across every competency dimension $D_k \in \{D_1, \dots, D_K\}$ (with rubric weight $w_k$ and target mastery level $L_{k, \text{target}} \in [1, 5]$):

#### 1. Epistemic Verification Delta ($\Delta_{\text{verify}}(c, k)$)

Measures whether the live LangGraph technical interview **confirmed**, **exceeded**, or **refuted** the candidate's Phase 3 CV claim $C_k^{\text{CV}}(c) \in [0, 1]$. Let $I_k^{\text{live}}(c) = \frac{M_{k, \text{achieved}}(c)}{5.0} \in [0, 1]$ be the normalized live interview mastery score for competency $D_k$. The **Verification Delta** $\Delta_{\text{verify}}(c, k) \in [-1, +1]$ is:

$$
\Delta_{\text{verify}}(c, k) = I_k^{\text{live}}(c) - C_k^{\text{CV}}(c)
$$

* $\Delta_{\text{verify}}(c, k) \ge +0.15$ **(`HIDDEN_GEM_UPGRADE`):** Candidate understated their expertise on their CV but proved deep architectural mastery during live Socratic probing.

* $\left\vert{} \Delta_{\text{verify}}(c, k) \right\vert{} < 0.15$ **(`CORROBORATED_CLAIM`):** Live interview performance is consistent with CV evidence.

* $\Delta_{\text{verify}}(c, k) \le -0.25$ **(`RESUME_INFLATION_WARNING`):** Candidate's CV claimed production ownership ($C_k^{\text{CV}}$ high), but the candidate collapsed under Socratic follow-up questioning ($I_k^{\text{live}}$ low).

#### 2. Posterior Bayesian-Weighted Competency Score ($S_k^{\text{post}}(c)$)

Once a candidate completes the live adaptive interview, empirical performance under Socratic probing carries significantly higher epistemic weight than static CV text. Given the number of evaluated turns $n_{c, k}$ spent probing competency $D_k$ and turn-level confidence $\gamma_{c, k} \in [0, 1]$, the **Posterior Competency Score** $S_k^{\text{post}}(c) \in [0, 100]$ blends the prior CV score $C_k^{\text{CV}}(c)$ with the live empirical score $I_k^{\text{live}}(c)$ via a precision-weighted update:

$$
S_k^{\text{post}}(c) = 100 \times \left( \left(1 - \rho_{c, k}\right) \cdot C_k^{\text{CV}}(c) + \rho_{c, k} \cdot I_k^{\text{live}}(c) \right), \quad \text{where } \rho_{c, k} = \frac{n_{c, k} \cdot \gamma_{c, k}}{\tau_0 + n_{c, k} \cdot \gamma_{c, k}}
$$

With prior inertia constant $\tau_0 = 0.50$. For a probed competency ($n_{c,k} \ge 2, \gamma_{c,k} \approx 0.90$), live interview evidence dominates ($\rho_{c,k} \approx 0.78$); for a minor nice-to-have competency skipped due to time-boxing ($n_{c,k} = 0$), $\rho_{c,k} = 0$ and the matrix transparently falls back to the verified CV prior $C_k^{\text{CV}}(c)$ tagged as `CV_PRIOR_ONLY`.

#### 3. Integrity-Adjusted Holistic Decision Index ($\mathcal{H}_{\text{final}}(c)$)

Let $\mathcal{A}_{\text{auth}}(c) \in [0, 1]$ be the candidate's overall **Session Authenticity & Integrity Index** synthesized by the Phase 4 `ScoringVerificationNode`. To prevent high-scoring but suspiciously assisted sessions from ranking #1 without warning, the holistic comparison index applies a non-linear authenticity confidence discount whenever $\mathcal{A}_{\text{auth}}(c)$ falls below safety threshold $\theta_{\text{safe}} = 0.85$:

$$
\mathcal{H}_{\text{final}}(c) = \left( \sum_{k=1}^{K} w_k \cdot S_k^{\text{post}}(c) \right) \times \min\left( 1.0, \, \left( \frac{\mathcal{A}_{\text{auth}}(c)}{\theta_{\text{safe}}} \right)^{\beta_{\text{int}}} \right)
$$

Where $\beta_{\text{int}} = 1.5$. Both the raw weighted technical score $\sum w_k S_k^{\text{post}}(c)$ and the integrity-adjusted index $\mathcal{H}_{\text{final}}(c)$ are displayed side-by-side so human hiring managers retain full visibility and final decision authority.

#### 4. Cross-Candidate Relative Advantage Matrix ($\Omega_{a, b}^{(k)}$)

For any pair of candidates $(c_a, c_b)$ in the selected comparison set of size $M \in [2, 4]$, the matrix highlights the **Dimension Winner** and **Pairwise Dominance Margin** $\Omega_{a, b}^{(k)}$:

$$
\Omega_{a, b}^{(k)} = w_k \cdot \left( S_k^{\text{post}}(c_a) - S_k^{\text{post}}(c_b) \right)
$$

Any cell where $S_k^{\text{post}}(c)$ is the maximum among the $M$ compared candidates AND satisfies $M_{k, \text{achieved}}(c) \ge L_{k, \text{target}}$ is badged as `DIMENSION_LEADER`.

### 3.3 Visual Architecture Diagram: Recruiter Comparison Matrix & Batch Telemetry UX Flow

```
sequenceDiagram
    autonumber
    actor Manager as Engineering Manager / Recruiter
    participant UI_Dash as Presentation: RecruiterDashboardView
    participant Store_Batch as Application: BatchTelemetryStore
    participant SSE_Ad as Infrastructure: EventSourceSSEAdapter
    participant UI_Mat as Presentation: ComparisonMatrixView
    participant UC_Comp as Application: ComputeCandidateComparisonUseCase
    participant Mat_Dom as Domain: ComparisonMatrixNormalizer
    participant REST_Ad as Infrastructure: FetchRestApiACLAdapter

    Note over Manager,SSE_Ad: Flow A: Real-Time 500-CV Batch Ingestion Monitoring
    Manager->>UI_Dash: Upload & Commit 500-CV Screening Batch
    UI_Dash->>Store_Batch: SubscribeToBatch(BatchID)
    Store_Batch->>SSE_Ad: OpenSSEStream(/api/v1/batches/{id}/events)
    loop Every 250ms Tumbling Window Until Batch Complete
        SSE_Ad-->>Store_Batch: Receive BatchProgressTelemetryEvent (Stage Counts, Velocity, New Top Matches)
        Store_Batch->>Store_Batch: Schedule commit on requestAnimationFrame (16.6ms)
        Store_Batch-->>UI_Dash: Emit Immutable Slice Update (60 FPS Virtualized Grid Render)
    end

    Note over Manager,REST_Ad: Flow B: Side-by-Side Candidate Comparison Matrix (2 to 4 Finalists)
    Manager->>UI_Mat: Select 3 Shortlisted Candidates [Cand_A, Cand_B, Cand_C] & Click "Compare"
    UI_Mat->>UC_Comp: Execute(CompareCandidatesQueryDTO)
    UC_Comp->>REST_Ad: POST /api/v1/requisitions/{req_id}/comparison-matrix
    REST_Ad-->>UC_Comp: Return Validated ComparisonMatrixDomainPayload
    UC_Comp->>Mat_Dom: ComputePairwiseDeltasAndRadarProjections(Payload, NormalizationMode)
    Mat_Dom-->>UC_Comp: Return NormalizedComparisonMatrixViewModel (Winners, InflationFlags, Citations)
    UC_Comp-->>UI_Mat: Render Interactive Matrix + Competency Radar + Socratic Transcript Drawer

    Manager->>UI_Mat: Click "Resume Inflation Warning (-32%)" Badge on Cand_B / Competency D_2
    UI_Mat->>UI_Mat: Open Synchronized Side Drawer Comparing CV Quote vs. Failed Socratic Turn #4

```

### 3.4 Conceptual View-Model Data Contracts (Frontend Presentation Contracts)

#### Table 3.4.1: `SideBySideComparisonMatrixViewModel` Conceptual Schema

| Field Name | Conceptual Data Type | Constraints & Validation Rules | UI Rendering & Decision Purpose | 
| ----- | ----- | ----- | ----- | 
| `RequisitionID` | `UUIDv7` | Must match active requisition context. | Scopes rubric headers and disposition actions. | 
| `ComparedCandidateColumns` | `OrderedList<CandidateColumnHeader>` | Cardinality $M \in [2, 4]$; ordered by descending $\mathcal{H}_{\text{final}}(c)$ by default. | Defines the $M$ vertical candidate columns in the sticky comparison header. | 
| `CandidateColumn.CandidateID` | `UUIDv7` | Unique within matrix. | Key for selection, transcript drawer drill-down, and disposition override. | 
| `CandidateColumn.DisplayIdentity` | `CandidateIdentityView` | Contains `RevealedFullName` (if user has `pii:read` permission) OR `AnonymizedAlias` (in Blind Hiring Mode). | Enforces optional **Blind Screening Mode** directly in the comparison UI. | 
| `CandidateColumn.CompositeCVMatchScore` | `DecimalFloat(2)` | Range $[0.00, 100.00]$ from Phase 3. | Baseline pre-interview match score. | 
| `CandidateColumn.PosteriorTechnicalScore` | `DecimalFloat(2)` | Range $[0.00, 100.00]$ ($\sum w_k S_k^{\text{post}}(c)$). | Empirical post-interview technical score before integrity discounting. | 
| `CandidateColumn.HolisticDecisionIndex` | `DecimalFloat(2)` | Range $[0.00, 100.00]$ ($\mathcal{H}_{\text{final}}(c)$). | Primary composite ranking metric combining rubric mastery and authenticity. | 
| `CandidateColumn.AuthenticityBadge` | `AuthenticityStatusTuple` | Tuple of `(AuthenticityIndex, SeverityTier, FlagCount)`. | Displays `VERIFIED_HIGH (96%)`, `REVIEW_ADVISORY (78%)`, or `HIGH_RISK_FLAGGED (52%)`. | 
| `CandidateColumn.RecommendedHireBand` | `RecommendationEnum` | Enum: `{STRONG_HIRE, HIRE, BORDERLINE_HOLD, NO_HIRE}`. | Deterministic recommendation derived from rubric mastery and gatekeepers. | 
| `CompetencyComparisonRows` | `OrderedList<CompetencyMatrixRow>` | Cardinality $K \in [3, 10]$; ordered by descending rubric weight $w_k$. | Each row represents one JD competency dimension across all $M$ candidates. | 
| `CompetencyRow.DimensionMetadata` | `DimensionHeaderInfo` | `DimensionID`, `DimensionName`, `NormalizedWeightPercent`, `TargetMasteryLevel` ($\in [1,5]$). | Left-hand pinned column displaying the JD requirement and target bar. | 
| `CompetencyRow.CandidateCells` | `Map<CandidateID, CompetencyCellView>` | Exactly $M$ entries per row. | Intersecting cell for Candidate $c$ on Competency $D_k$. | 
| `CompetencyCell.PosteriorScore` | `DecimalFloat(1)` | Range $[0.0, 100.0]$ ($S_k^{\text{post}}(c)$). | Primary numeric bar and color-coded mastery indicator in the cell. | 
| `CompetencyCell.AchievedvsTargetDepth` | `DepthComparisonTuple` | `(AchievedLevel: Float[1..5], TargetLevel: Int[1..5], SocraticTurnsCount: Int)`. | Shows e.g. *"Level 4.2 / 4.0 Target (Verified via 2 Socratic Probes)"*. | 
| `CompetencyCell.VerificationDeltaBadge` | `VerificationDeltaObject` | Contains `DeltaValue` ($\Delta_{\text{verify}} \in [-1, +1]$) and `Classification` (`HIDDEN_GEM_UPGRADE`, `CORROBORATED_CLAIM`, `RESUME_INFLATION_WARNING`, `CV_PRIOR_ONLY`). | Instantly surfaces where the live interview exposed resume bluffing or discovered hidden talent. | 
| `CompetencyCell.IsDimensionLeader` | `BooleanFlag` | `TRUE` if highest score in row AND meets `TargetMasteryLevel`. | Highlights the strongest candidate for each specific technical pillar. | 
| `CompetencyCell.EvidenceDrilldownRef` | `EvidenceCitationBundle` | Contains `VerbatimCVQuote`, `KeyInterviewQuestionAsked`, `VerbatimCandidateAnswerExcerpt`, and `EvaluatorJustification`. | Populates the instant slide-over **Evidence Comparison Drawer** when a manager clicks any matrix cell. | 

### 3.5 Accessibility, Progressive Degradation & Ergonomic UX Guardrails

1. **Zero-Penalty Accommodated Modality Switching:** Candidates with speech impediments, hearing impairments, or severe microphone hardware failures can toggle **Accommodated Hybrid/Text Mode** during `HARDWARE_PREFLIGHT`. When active, the frontend disables the acoustic cadence anomaly penalty ($S_{\text{cadence}, t} = 0$) so the candidate is never falsely flagged for not speaking aloud, while maintaining standard rubric depth evaluation and paste-burst telemetry.

2. **Virtualized High-Density Table Rendering:** The Recruiter Batch Monitor and Candidate Pipeline Table use viewport windowing (rendering only the $\approx 25$ visible DOM rows out of $500+$ candidates) to ensure sub-$16\text{ ms}$ scroll frame times and constant memory usage regardless of batch volume.

3. **Synchronized Audio-Transcript Scrubbing in Evidence Drawer:** When a Hiring Manager clicks a cell in the **Side-by-Side Candidate Comparison Matrix**, the Evidence Drawer loads the exact timestamped utterance from the interview recording alongside the verbatim CV claim and the `EvaluationNode`'s structured rubric breakdown, enabling $15\text{-second}$ human verification of any AI scoring decision.