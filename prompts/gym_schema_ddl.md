Grafana dashboard source: gym-stats-smartass-codex / gym-statistics-final

Observed analytics schema in smartass.gym:

CREATE TABLE gym.organization (
    organization_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE gym.branch (
    branch_pk BIGINT PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES gym.organization(organization_id),
    name TEXT NOT NULL
);

CREATE TABLE gym.studio (
    studio_id BIGINT PRIMARY KEY,
    name TEXT
);

CREATE TABLE gym.workout_type (
    workout_type_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE gym.instructor (
    instructor_id BIGINT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE gym.participant (
    participant_id BIGINT PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT
);

CREATE TABLE gym.training_session (
    training_id BIGINT PRIMARY KEY,
    organization_id BIGINT REFERENCES gym.organization(organization_id),
    branch_pk BIGINT REFERENCES gym.branch(branch_pk),
    studio_id BIGINT REFERENCES gym.studio(studio_id),
    workout_type_id BIGINT REFERENCES gym.workout_type(workout_type_id),
    instructor_id BIGINT REFERENCES gym.instructor(instructor_id),
    starts_at TIMESTAMPTZ NOT NULL,
    registered_members INTEGER,
    visited_members INTEGER,
    max_members INTEGER
);

CREATE TABLE gym.training_participation (
    training_id BIGINT NOT NULL REFERENCES gym.training_session(training_id),
    participant_id BIGINT NOT NULL REFERENCES gym.participant(participant_id),
    status TEXT,
    attended_at TIMESTAMPTZ,
    is_new BOOLEAN,
    is_new_class BOOLEAN
);

CREATE TABLE gym.stg_training_schedule (
    training_id BIGINT,
    imported_at TIMESTAMPTZ,
    date TEXT,
    time TEXT,
    visited_members INTEGER,
    max_members INTEGER,
    studio TEXT
);

Observed business semantics from the dashboard:
- Always query schema gym explicitly.
- Attendance means gym.training_participation.attended_at IS NOT NULL.
- Join participation with sessions on gym.training_participation.training_id = gym.training_session.training_id.
- Use gym.training_session.starts_at for session dates, day buckets, week buckets, retention windows, and time-slot analysis.
- organization / branch / instructor / workout type filtering is usually done through gym.training_session columns: organization_id, branch_pk, instructor_id, workout_type_id.
- Session-level occupancy metrics live on gym.training_session:
  - registered_members = registrations
  - visited_members = attended count
  - max_members = session capacity
- Fill rate usually means visited_members / max_members.
- Registration fill rate means registered_members / max_members.
- Attendance rate usually means visited_members / registered_members.
- No-show rate usually means (registered_members - visited_members) / registered_members.
- Cancellation statuses start with 'cancelled'; known statuses include 'cancelled with return' and 'cancelled without return'.
- New attendee share uses gym.training_participation.is_new for attended participants.
- First-time class share uses gym.training_participation.is_new_class for attended participants.
- Top members by visits count only rows where attended_at IS NOT NULL.
- "Hasn't been here for more than 6 months" means MAX(attended_at) < NOW() - INTERVAL '6 months'.
- Retention 30 / 60 / 90 days uses each participant's first attended session date and checks whether they attended again at least 30 / 60 / 90 days later.
- For studio occupation trend or daily fill rate sourced from imported schedule snapshots, use gym.stg_training_schedule, parse date + time into a timestamp, and deduplicate by training_id keeping the latest imported_at row.
