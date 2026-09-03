-- The lead list needs a Projects column (docs/10:315) and the only surviving
-- copy of the ids was inside the encrypted `brief` envelope, so the list could
-- have rendered it only by decrypting buyer-derived data on every page view -
-- the thing the envelope exists to confine to the detail page.
--
-- Inventory ids are OUR ids: a closed set from data/inventory.json, already
-- public in the catalogue, and every one of them has to resolve through the
-- inventory loader (docs/02-). Nothing about a buyer is recoverable from
-- ['skyrise'], which is what makes a plain column the right answer here and
-- the wrong answer for `LeadBrief.shortlist_ids`, which sits in a
-- model-inferred record about a person.
--
-- A new file rather than an edit to 0001/0002: both are merged and applied.
ALTER TABLE leads ADD COLUMN project_ids text[] NOT NULL DEFAULT '{}';

-- The list filters on it (toby's PR), so it is worth an index rather than a
-- sequential scan per page once there are enough leads to page.
CREATE INDEX leads_project_ids ON leads USING gin (project_ids);
