# Reference contract identifying one immutable native message instance
# or one producer-defined immutable block.

# Namespaced reference scheme.
# The scheme defines ID namespace, resolution semantics and lifetime rules.
string scheme

# Opaque identity interpreted only according to scheme.
string id

# Namespaced payload interpretation contract.
# The profile may explicitly state that unit/layout/encoding semantics
# are unresolved.
string profile
