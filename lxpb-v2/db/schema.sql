-- Generic per-row review-state store shared by every report.
--
-- report_key: the report's existing storage-key string (e.g. the old
--             localStorage key, such as 'lxpb_labels_v1' or a stop/target
--             combo's derived key).
-- row_key:    the report's existing content-derived data-key for that row
--             (stable across regenerations).
-- fields:     whatever reviewed/valid/replayed/notes/misc/checkbox-id
--             object that particular report already tracks -- field sets
--             differ per report (some have dynamic per-report checkbox
--             ids), so this is stored as a JSONB bag rather than fixed
--             columns.
create table if not exists row_state (
  report_key text not null,
  row_key text not null,
  fields jsonb not null default '{}',
  updated_at timestamptz not null default now(),
  primary key (report_key, row_key)
);

create index if not exists row_state_report_idx on row_state (report_key);
