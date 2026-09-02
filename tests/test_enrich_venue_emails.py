"""Tests for venue email enrichment (search + contact page fetch)."""

from __future__ import annotations

import unittest

from scrapers.enrich_venue_emails import (
    EMAIL_VERIFIED_SEEDS,
    apply_result_to_row,
    apply_seed,
    email_without_source_is_invalid,
    extract_emails_from_html,
    extract_emails_with_sources,
    first_venue_domain,
    is_empty_shell_html,
    is_junk_email,
    is_rejected_domain,
    normalize_site_root,
    pick_best_email,
    review_row_from_location,
    unwrap_ddg_href,
)


class TestEmailHelpers(unittest.TestCase):
    def test_reject_aggregators(self):
        self.assertTrue(is_rejected_domain("https://www.yelp.com/biz/foo"))
        self.assertTrue(is_rejected_domain("https://www.opentable.com/r/foo"))
        self.assertFalse(is_rejected_domain("https://ruffiannyc.com/contact"))

    def test_unwrap_ddg(self):
        href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fruffiannyc.com%2F"
        self.assertEqual(unwrap_ddg_href(href), "https://ruffiannyc.com/")

    def test_normalize_site_root(self):
        self.assertEqual(
            normalize_site_root("https://www.ruffiannyc.com/contact"),
            "https://ruffiannyc.com",
        )

    def test_first_venue_domain_skips_yelp(self):
        urls = [
            "https://www.yelp.com/biz/ruffian",
            "https://ruffiannyc.com/",
        ]
        self.assertEqual(first_venue_domain(urls), "https://ruffiannyc.com")

    def test_extract_mailto_and_bare(self):
        html = """
        <html><body>
          <a href="mailto:hello@cherryontopnyc.com">Email</a>
          <p>Reach us at covenhoven@gmail.com for events.</p>
        </body></html>
        """
        emails = extract_emails_from_html(html)
        self.assertIn("hello@cherryontopnyc.com", emails)
        self.assertIn("covenhoven@gmail.com", emails)

    def test_extract_emails_with_sources_tracks_page(self):
        html = "<a href='mailto:info@fourhorsemenbk.com'>Email</a>"
        page_url = "https://fourhorsemenbk.com/contact"
        sources = extract_emails_with_sources(html, page_url)
        self.assertEqual(sources["info@fourhorsemenbk.com"], page_url)

    def test_accepts_gmail_when_published(self):
        sources = {
            "ruffiannyc@gmail.com": "https://ruffiannyc.com/contact",
            "noreply@wixpress.com": "https://ruffiannyc.com/",
        }
        email, url = pick_best_email(sources, "ruffiannyc.com")
        self.assertEqual(email, "ruffiannyc@gmail.com")
        self.assertEqual(url, "https://ruffiannyc.com/contact")

    def test_prefix_preference(self):
        sources = {
            "reservations@askanyc.com": "https://askanyc.com/reservations",
            "hello@askanyc.com": "https://askanyc.com/contact",
            "owner@askanyc.com": "https://askanyc.com/about",
        }
        email, url = pick_best_email(sources, "askanyc.com")
        self.assertEqual(email, "hello@askanyc.com")
        self.assertEqual(url, "https://askanyc.com/contact")

    def test_junk_filters(self):
        self.assertTrue(is_junk_email("logo@2x.png"))
        self.assertTrue(is_junk_email("noreply@example.com"))
        self.assertFalse(is_junk_email("delivery@peoples.wine"))

    def test_empty_shell(self):
        shell = "<html><head><script src='/app.js'></script></head><body></body></html>"
        rich = (
            "<html><body><h1>Contact us</h1>"
            "<p>Email info@fourhorsemenbk.com for reservations, events, and general inquiries. "
            "We are open Tuesday through Sunday for dinner service in Brooklyn.</p>"
            "</body></html>"
        )
        self.assertTrue(is_empty_shell_html(shell))
        self.assertFalse(is_empty_shell_html(rich))

    def test_email_without_source_is_invalid(self):
        self.assertTrue(email_without_source_is_invalid("info@test.com", ""))
        self.assertFalse(email_without_source_is_invalid("", ""))
        self.assertFalse(
            email_without_source_is_invalid("info@test.com", "https://test.com/contact")
        )


class TestApplyResult(unittest.TestCase):
    def test_crawl_writes_staging_not_live(self):
        from scrapers.enrich_venue_emails import EmailEnrichmentResult

        row = {"name": "TEST", "email": ""}
        result = EmailEnrichmentResult(
            email="hello@test.com",
            email_source_url="https://test.com/contact",
            email_source="venue_site",
        )
        self.assertTrue(apply_result_to_row(row, result))
        self.assertEqual(row["staging_email"], "hello@test.com")
        self.assertEqual(row["staging_email_source_url"], "https://test.com/contact")
        self.assertEqual(row.get("email"), "")

    def test_refuses_email_without_source_url(self):
        from scrapers.enrich_venue_emails import EmailEnrichmentResult

        row = {"name": "TEST"}
        result = EmailEnrichmentResult(email="hello@test.com", email_source="venue_site")
        self.assertFalse(apply_result_to_row(row, result))
        self.assertNotIn("staging_email", row)

    def test_review_row_prefers_staging(self):
        row = {
            "name": "TEST",
            "email": "old@live.com",
            "email_source_url": "https://old.com",
            "staging_email": "new@staging.com",
            "staging_email_source_url": "https://new.com/contact",
        }
        review = review_row_from_location(row)
        self.assertEqual(review["email"], "new@staging.com")
        self.assertEqual(review["source_url"], "https://new.com/contact")


class TestVerifiedSeeds(unittest.TestCase):
    def test_all_seeds_present(self):
        expected = {
            "ASKA",
            "FOUR HORSEMEN",
            "RUFFIAN",
            "HANA MAKGEOLLI",
            "PEOPLES WINE",
            "COVENHOVEN",
            "CHERRY ON TOP",
            "BAR MERIDIAN",
        }
        self.assertTrue(expected <= set(EMAIL_VERIFIED_SEEDS.keys()))

    def test_seeds_have_source_url_when_email_set(self):
        for key, seed in EMAIL_VERIFIED_SEEDS.items():
            if seed.get("email"):
                self.assertTrue(
                    seed.get("email_source_url"),
                    f"{key} has email but no email_source_url",
                )

    def test_ruffian_seed(self):
        row = {"name": "RUFFIAN"}
        result = apply_seed(row)
        self.assertIsNotNone(result)
        self.assertEqual(result.email, "ruffiannyc@gmail.com")
        self.assertEqual(result.website, "https://ruffiannyc.com")
        self.assertEqual(result.email_source_url, "https://ruffiannyc.com/contact")

    def test_bar_meridian_headless_flag(self):
        row = {"name": "BAR MERIDIAN"}
        result = apply_seed(row)
        self.assertIsNotNone(result)
        self.assertTrue(result.email_needs_headless)
        self.assertEqual(result.email, "")


if __name__ == "__main__":
    unittest.main()
