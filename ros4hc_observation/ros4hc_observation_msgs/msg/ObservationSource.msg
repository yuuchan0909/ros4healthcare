# device_type must be nonempty ("unknown" allowed); device_id/node may be empty.
string device_type
string device_id
# Resolved input topic, canonical package/msg/Type and nonempty field path.
# These identify a stream/field, not a unique source sample or storage record.
string topic
string message_type
string field_path
string node
