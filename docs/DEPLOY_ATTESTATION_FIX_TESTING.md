# Attestation regression coverage

`tests/test_deploy_attestation_workflow.py` and `tests/test_deploy_attestation_sample.py` assert that deployment attestation uses both deploy/startup output and the refreshed status snapshot for startup markers, while keeping SHA and service-state checks tied to the current status output.
