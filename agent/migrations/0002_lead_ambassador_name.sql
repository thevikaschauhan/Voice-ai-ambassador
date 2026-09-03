-- `LeadSnapshot.ambassador_name` had no column. A NEW migration rather than an
-- edit to 0001: that file is merged and has already been applied to the
-- Supabase project, so changing it would mean the schema a deployed database
-- carries no longer matches the file that claims to describe it.
--
-- Which ambassador answered is part of the lead: the client chose three names,
-- one per language, and an admin reading a call needs to know who the buyer
-- believes they spoke to.
ALTER TABLE leads ADD COLUMN ambassador_name text NOT NULL DEFAULT '';
