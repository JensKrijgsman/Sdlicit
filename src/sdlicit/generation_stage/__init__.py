"""Generation stage — BDD persona creation and Gherkin generation.

The BDD Facilitator agent takes all prior context (ADRs, SRS, agent
reviews) and:
  1. Generates user personas
  2. Validates personas with other agents and the user
  3. Converts validated personas + requirements to BDD Gherkin scenarios
"""
