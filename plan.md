Below is an **end-to-end plan** for delivering an **Single-customer, real-time transcription demo** using **`nvidia/parakeet-tdt-0.6b-v3`**, focused on outcomes, milestones, and decision points.

---

## Objective

Deliver a **polished, low-latency live transcription demo** for one customer that:

- Feels responsive and stable in real time
- Demonstrates production intent (not a research script)
- Can later evolve into a multi-tenant or scaled system without redesign

Success is defined by **customer perception**, not benchmark scores.

---

## Scope (Intentionally Constrained)

### Included

- Live microphone → real-time transcription
- Partial + final text streaming
- Automatic language detection
- Simple browser-based UI
- Single GPU, single service

### Explicitly Excluded (for now)

- Auth, billing, multi-tenant isolation
- Long-term transcript storage

---

## High-Level Architecture

**One service, one GPU, one customer**

```
Browser (Web UI)
   │
   │ WebSocket (audio + events)
   ▼
Single ASR Service
   ├─ WebSocket server
   ├─ Session manager (1 active stream)
   ├─ Audio chunker + endpointing
   ├─ NeMo streaming inference
   └─ GPU (model loaded once)
```

No gateway, no queue, no autoscaling. Everything runs in one process or container.

---

## Phased Delivery Plan

### Phase 1 — Experience Definition

**Goal**: Lock what the customer will see and how it behaves.

Deliverables:

- UX rules:

  - Partial text updates frequently
  - Final text only on pauses or stop
  - No visible “text thrashing”

- Simple controls:

  - Start / Stop

- Two modes: **Fast** vs **Accurate** (internally mapped to chunking presets)

---

### Phase 2 — Service Skeleton

**Goal**: Prove the service lifecycle without worrying about model quality yet.

Deliverables:

- WebSocket endpoint with:

  - session start
  - streaming audio ingestion
  - clean stop / teardown

- Session state machine:

  - INIT → STREAMING → FINALIZING → CLOSED

- Deterministic behavior on:

  - client disconnect
  - stop mid-sentence
  - reconnect (new session, clean transcript)

Exit criteria:

- Audio can stream continuously without crashes
- Sessions always terminate cleanly
- No GPU or memory leaks across start/stop cycles

---

### Phase 3 — Streaming ASR Integration

**Goal**: Achieve convincing real-time transcription behavior.

Deliverables:

- NeMo streaming inference wired into the service
- Chunking + context configured for:

  - sub-second partials
  - stable finals

- Automatic language detection surfaced once per session
- Simple endpointing (silence-based is sufficient)

Quality bar:

- First partial appears quickly after speech starts
- Pauses reliably finalize sentences
- Output feels stable to a non-technical observer

Exit criteria:

- Real-time factor comfortably < 1.0
- No noticeable lag buildup over multi-minute speech
- GPU memory stable during long sessions

---

### Phase 4 — Demo-Grade UX

**Goal**: Make it _look_ professional.

Deliverables:

- Clean single-page web UI
- Visual distinction:

  - Final text vs partial text

- Connection status indicator
- Clear error messages (no stack traces)

Exit criteria:

- A first-time user can use it without explanation
- Demo survives flaky Wi-Fi or tab reloads gracefully

---

### Phase 5 — Demo Hardening

**Goal**: Remove “demo risk.”

Deliverables:

- Model warm-up on startup
- Guardrails:

  - max session length
  - max audio buffer

- Logging for:

  - latency
  - session start/stop
  - errors

- One-command startup (docker run or single script)

Exit criteria:

- You can demo it repeatedly without restarting the service
- No visible degradation after multiple runs

---

## Demo Readiness Checklist

Before showing the customer:

- [ ] Cold start < ~10 seconds (or hidden with “warming up”)
- [ ] First text appears quickly after speech
- [ ] Finals occur naturally on pauses
- [ ] Transcript is readable and clean
- [ ] Stop always produces a final transcript
- [ ] If something fails, the UI explains it politely

---

## Technology Choices (Locked for This Plan)

- **Transport**: WebSocket
- **Client**: Browser (mic capture)
- **Model runtime**: NeMo streaming pipeline
- **Deployment**: Single container / single GPU host
- **Target hardware**: ≥16 GB VRAM GPU

No alternative stacks, no parallel paths.
