# Phase 4A Observation evaluation

This is evaluation-only research code. It examines common metadata representation,
exact information preservation, explicit UNKNOWN/unavailable states, and source
traceability across existing Scalar, Vector and Reference observations. It makes
no changes to core Observation schemas, validators, adapters or resolver semantics.

Representative synthetic cases reuse actual implementations:

- Polar HR → existing HR mapping → existing v0.2 bridge → ScalarObservation.
- Polar ACC → existing mapping → VectorObservation.
- Sensomative RawNotification → existing mapping/registration → ReferenceObservation
  → existing resolver, same instance in the same process → native message recovery.

The ACC mapper's existing `g` / INFERRED status is reported as supplied; this
package neither establishes nor changes its physical interpretation. Sensomative
bytes are not decoded, calibrated, reshaped, or assigned pressure units.

## Architecture and report meaning

`evaluation.py` is pure Python, with no ROS imports or node startup. It accepts
existing v0.2 Python models and generated messages. `demo.py` contains the sensor
case dependencies and synthetic inputs; imports of generated RawNotification and
its production mapper are local to the Sensomative case. The package explicitly
declares these dependencies for a reproducible three-case demo. No production
package depends on this evaluation package.

- `metadata_facts`: representation, identity values/presence, observation type,
  unit/status, primary stamp/semantics/clock/event, all source fields, data level,
  validity and quality facts.
- `metadata_completeness`: field-level `present`, `empty`, `UNKNOWN`,
  `unavailable` (and `unrecognized` enum code). It is descriptive, not a score.
  Quality scale can be empty while quality value is unavailable. These independent
  states are deliberate. Missing required identities can still fail the existing
  validator; this evaluator never repairs them.
- `unknown_semantics`: explicit flags for unknown units/validity, empty unit/frame,
  unavailable quality and the NaN/empty-scale unavailable quality contract. These
  flags report states, not failures. They do not prove preservation from a source
  unless compared against a source expectation elsewhere.
- `evaluate_scalar` / `evaluate_vector`: numeric exact equality, per-axis results,
  explicit expected frame comparison, and unchanged core validation errors.
  Paired NaNs count as preserved, a single NaN mismatches, infinity always fails.
  No tolerance, unit conversion or fabricated frame is used. `exact` means numeric
  preservation, not bitwise IEEE equality (e.g. signed zeros compare equal).
  `pass` also requires the existing validator to accept the observation: NaN with
  VALID therefore stays invalid even if exactly preserved. MISSING requires NaN
  in all components, as defined by the existing validator. No measurement validity
  is inferred from a passing structural/preservation check.
- `reference_descriptor`: scheme, opaque ID and versioned profile, unmodified.
- `evaluate_reference`: requires the registering resolver instance; resolves with
  source.message_type, compares native Python type and every generated native
  field recursively, including Time, all bytes and payload length. Reports known
  resolver errors without converting them into measurement quality. This native
  comparator targets RawNotification's fields, not arbitrary float-rich payloads.
- `evaluation_report`: common sections, with comparisons marked `evaluated: false`
  until explicitly supplied. `report_json`: strict JSON; non-finite numbers are
  strings `"NaN"`, `"Infinity"`, `"-Infinity"`, never nonstandard JSON literals.

There is no single completeness percentage, performance metric, aggregate research
score, or inference about clinical accuracy. Empty optional fields, UNKNOWN and
unavailable are legitimate semantic outcomes. Source fields report asserted
provenance; the evaluator does not authenticate device identities or timestamps.
The report extracts primary time; additional times remain in the original
observation and are not comprehensively evaluated in this phase.

## Reference limits

Recovery scope is explicitly `same_process_same_resolver_instance_only`.
A fresh resolver instance cannot recover the registration even in the same
process. A separate subscriber receives only the descriptor and cannot access
this store. No cross-process recovery, persistence, restart recovery, clinical
validity, device timing, sensor layout, calibration or production readiness is
claimed. The production in-memory resolver's copy isolation and limitations are
unchanged. No alternate resolver is implemented here.

## Isolated build, test and demo

Ubuntu 22.04 / ROS 2 Humble. Use a fresh shell. These commands leave
`build_observation` and `install_observation` untouched. Explicit package search
roots exclude unrelated packages such as untracked `apple_watch_replay`.

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon --log-base log_observation_eval build \
  --base-paths src/ros4healthcare/ros4hc_observation \
    src/ros4healthcare/ros4hc_drv/sensomative_raw_bridge \
    src/ros4healthcare/ros4hc_drv/sensomative_raw_msgs \
    src/ros4healthcare/ros4hc_msgs \
  --build-base build_observation_eval \
  --install-base install_observation_eval \
  --packages-up-to ros4hc_observation_eval
source install_observation_eval/setup.bash
```

Run the focused suite via colcon and inspect its result:

```bash
colcon --log-base log_observation_eval test \
  --base-paths src/ros4healthcare/ros4hc_observation \
  --build-base build_observation_eval \
  --install-base install_observation_eval \
  --packages-select ros4hc_observation_eval
colcon test-result --test-result-base build_observation_eval --verbose
```

For explicit unittest counts and existing regression suites:

```bash
cd ~/ros2_ws/src/ros4healthcare
python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_eval/test -v
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_observation/ros4hc_observation_tools/test -v
ROS_LOCALHOST_ONLY=1 python3 -m unittest discover -s ros4hc_drv/sensomative_raw_bridge/test -v
```

The demo starts no ROS node and requires no network, Windows or hardware:

```bash
ros2 run ros4hc_observation_eval observation_eval_demo > /tmp/observation_eval_report.json
python3 -m json.tool /tmp/observation_eval_report.json
```

Tests cover model and generated messages, NaN/finite/infinity behavior against
existing validators, empty frame preservation, factual metadata, strict JSON,
descriptor reporting, every RawNotification field (including deliberate mutations),
copy isolation and rejection of a fresh resolver instance. Synthetic results are
contract demonstrations, not empirical generalization across devices or datasets.
Extensibility here means reporting existing payload variants through the common
metadata API without core schema edits; no new payload schema is proposed.
