uint8 VALIDITY_UNKNOWN=0
uint8 VALIDITY_VALID=1
uint8 VALIDITY_INVALID=2
uint8 VALIDITY_MISSING=3
uint8 VALIDITY_STALE=4
uint8 VALIDITY_DUPLICATE=5

# UNKNOWN is legitimate; structural validity is not physiological validity.
uint8 validity
# Unavailable: NaN value and empty scale. Available: finite value and native scale.
bool quality_available
float64 quality_value
string quality_scale
