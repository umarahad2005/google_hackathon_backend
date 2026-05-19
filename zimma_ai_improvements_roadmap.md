# 🚀 Zimma AI — Next Level Improvements

Now that the foundational architecture is complete and deployed (backend on FastAPI Cloud, frontend wired up), here is the roadmap to elevate Zimma AI from a great hackathon project to a **production-ready, state-of-the-art product**.

---

## 1. 🧠 Advanced Agentic Capabilities (Backend)

The Gemini ADK Orchestrator is powerful, but we can make it truly autonomous and multimodal.

* **Vision / Multi-modal Intents (The "Show Me" Feature):** 
  * *What:* Allow users to upload a photo of the broken AC or leaking pipe along with their voice note.
  * *How:* Upgrade the `Intent/NLU Agent` to accept multimodal input (`gemini-2.5-pro-vision`). The agent can diagnose the issue from the image and automatically deduce the required service category and urgency without the user explicitly stating it.
* **Conversational Memory:**
  * *What:* The Orchestrator currently treats every request in isolation. Add a `Memory Agent` or thread-context to recall past interactions.
  * *Example:* If a user says "Cancel the AC technician and get a plumber instead", the agent should understand the context and update the state machine.
* **Price Negotiation Sub-Agent:**
  * *What:* Add a new step in the workflow: `RANKING` → `NEGOTIATING` → `RECOMMENDED`. 
  * *How:* Before confirming the booking, the agent negotiates with the top provider API to get a price within the user's budget.
* **Automated Feedback & QA Agent:**
  * *What:* After the `COMPLETED` state, an agent reaches out via WhatsApp/SMS to ask for feedback in Urdu. It parses the natural language response and converts it into a numeric rating.

## 2. 📱 UX & UI Polish (Flutter Frontend)

The current UI is clean, but we can make it feel magical.

* **Interactive Live Maps:** 
  * *What:* On the Recommendation screen, add an embedded Google Map showing the provider's location relative to the user's sector (e.g., G-13).
  * *How:* Use `google_maps_flutter` to render the coordinates returned by the `Provider Discovery Agent`.
* **Micro-Animations for Live Trace:**
  * *What:* Enhance the `TraceTimeline` screen with dynamic Lottie animations for each agent phase (e.g., a radar sweeping during `DISCOVERING`, scales tipping during `RANKING`).
* **Text-to-Speech (TTS) Voice Responses:**
  * *What:* Since the user can *speak* to the app, the app should speak back.
  * *How:* Use the device's native TTS or a service like Google Cloud TTS to read the final recommendation and reasoning out loud in Urdu or English.
* **Push Notifications:**
  * *What:* Instead of making the user stare at the Follow-up screen, send push notifications when the `Follow-up Agent` changes the status (e.g., "The technician is en route!").

## 3. 🏗️ Infrastructure & Scalability

To support thousands of users, the infrastructure needs optimization.

* **Google Maps Response Caching (Redis):**
  * *What:* The `Provider Discovery Agent` hits the Places API and Distance Matrix API heavily.
  * *How:* Cache frequent queries (e.g., "AC Technicians in G-13") in Redis. This drastically reduces Google Maps quota usage and drops discovery latency from 1.5s to 50ms.
* **Supabase Edge Functions:**
  * *What:* Offload simple background tasks (like cleaning up stale bookings or syncing new providers from Google Maps into PostGIS) to Supabase Edge Functions instead of running them on the FastAPI server.
* **Strict JWT Authentication (RLS):**
  * *What:* Currently, the backend allows unauthenticated calls to easily test the app.
  * *How:* Enforce strict JWT validation on the FastAPI endpoints and apply Row Level Security (RLS) on Supabase so `ServiceRequests` and `Traces` can *only* be read by the user who created them.

## 4. 🧪 Hardening (Completing Phase 5 & 6)

* **Chaos Testing:** Simulate network failures. What happens if the Supabase Realtime SSE stream disconnects during the Trace Timeline? The Flutter app should gracefully fallback to HTTP polling.
* **Multilingual End-to-End Suite:** Write automated tests that fire 50 different variations of Roman Urdu, strict Urdu script, and English intents to guarantee the `Intent Agent` never misclassifies a service.
