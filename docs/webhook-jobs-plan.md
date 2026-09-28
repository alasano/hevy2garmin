# Workout-specific webhook jobs

1. Keep the existing bearer authentication. Validate the UUID in the flat
   `{"workoutId": "..."}` payload shown in Hevy's developer settings; reject malformed requests without scheduling
   a general sync.
2. Store one job per workout ID in the existing SQLite/Postgres database, with
   status, attempt count, next attempt time and last error. Duplicate deliveries
   must not restart an active job's timer. Queue bursts instead of dropping them.
3. Run a single background worker in the existing server process. Resume due jobs
   on startup, stop the worker on shutdown, and use the existing sync lock to
   serialize Garmin work with manual and automatic sync. Include the per-workout
   manual route in that lock and remove time-based stealing of a live lock. Run
   one server process, as required by the existing in-process sync coordination.
   Give the worker its own database connection. Do not add a queue service.
4. First attempt after 30 seconds, then retry every 300 seconds, up to 24 attempts.
   Fetch only the job's workout by ID on each attempt. Already-synced workouts
   finish without another merge; pending uploads retain their existing ownership.
5. Reuse the existing merge implementation with a strict merge-only guard, including
   when merge mode is disabled. Missing Garmin matches and temporary API failures
   retry; authentication/configuration failures stop with a recorded error.
   Reset the merge circuit breaker each attempt and retain typed merge failures
   so wrapped authentication errors can be classified correctly.
   Exhausted jobs leave normal auto-sync as the safety net. A busy sync lock does
   not consume an attempt.
6. Make the fetch-by-ID helper distinguish a 404 from API failures. Include the
   workout ID and job outcome in logs; retain job status and errors in storage.
7. Update webhook documentation and tests for payload validation, exact-ID lookup,
   deduplication, timing, restart recovery, error handling, and both database backends.
8. Run relevant tests and the full suite, commit and push to fork/main, then build
   and publish registry.aljosa.ca/hevy2garmin:latest and a commit-specific tag for
   the existing linux/amd64 deployment. Verify the published image digest.

This changes new-workout webhook processing. It does not introduce automatic
re-synchronization of edits to workouts already recorded as synced.

An Astra review identified the manual-route lock gap, the per-attempt circuit
breaker reset, and loss of typed Garmin errors. These corrections are included
above; a distributed queue or distributed locking is unnecessary for this deployment.
