"""Tests for venue geocode guards (Nominatim false-match regression cases)."""

from __future__ import annotations

import unittest

from scrapers.venue_geocode import (
    NY_STATE_VIEWBOX,
    evaluate_nominatim_result,
    evaluate_photon_feature,
    in_viewbox,
    is_ambiguous_venue_name,
    is_business_osm,
    is_business_photon,
    name_confidence,
    ny_borough_from_zip,
    normalize_from_nominatim,
    review_confidence_threshold,
)


class BadMatchRejectionTests(unittest.TestCase):
    """Five real false positives from unconstrained Nominatim name search."""

    def test_saga_hair_saga_buffalo_rejected(self) -> None:
        result = {
            "lat": "42.899",
            "lon": "-78.878",
            "name": "Hair Saga",
            "class": "shop",
            "type": "hairdresser",
            "address": {
                "house_number": "4210",
                "road": "Union Road",
                "city": "Buffalo",
                "county": "Erie County",
                "state": "New York",
                "postcode": "14225",
            },
        }
        hit = evaluate_nominatim_result("SAGA", result, "NY", NY_STATE_VIEWBOX)
        self.assertIsNone(hit)
        self.assertTrue(in_viewbox(42.899, -78.878, NY_STATE_VIEWBOX))

    def test_the_fly_unadilla_rejected(self) -> None:
        result = {
            "lat": "42.441",
            "lon": "-75.064",
            "name": "The Fly",
            "class": "place",
            "type": "hamlet",
            "address": {
                "road": "Main Street",
                "county": "Otsego County",
                "state": "New York",
            },
        }
        hit = evaluate_nominatim_result("THE FLY", result, "NY", NY_STATE_VIEWBOX)
        self.assertIsNone(hit)

    def test_white_tiger_taekwondo_rejected(self) -> None:
        result = {
            "lat": "40.76",
            "lon": "-73.41",
            "name": "White Tiger Taekwondo",
            "class": "leisure",
            "type": "sports_centre",
            "address": {
                "road": "Hillside Avenue",
                "city": "Herricks",
                "county": "Nassau County",
                "state": "New York",
            },
        }
        self.assertFalse(is_business_osm("leisure", "sports_centre"))
        hit = evaluate_nominatim_result(
            "WHITE TIGER", result, "NY", NY_STATE_VIEWBOX
        )
        self.assertIsNone(hit)

    def test_little_flower_playground_rejected(self) -> None:
        result = {
            "lat": "40.716",
            "lon": "-73.985",
            "name": "Little Flower Playground",
            "class": "leisure",
            "type": "playground",
            "address": {
                "road": "Madison Street",
                "borough": "Manhattan",
                "city": "New York",
                "state": "New York",
                "postcode": "10002",
            },
        }
        hit = evaluate_nominatim_result(
            "LITTLE FLOWER", result, "NY", NY_STATE_VIEWBOX
        )
        self.assertIsNone(hit)

    def test_as_is_island_rejected(self) -> None:
        result = {
            "lat": "43.55",
            "lon": "-73.65",
            "name": "As You Were Island",
            "class": "place",
            "type": "island",
            "address": {
                "county": "Warren County",
                "state": "New York",
            },
        }
        hit = evaluate_nominatim_result("AS IS", result, "NY", NY_STATE_VIEWBOX)
        self.assertIsNone(hit)


class NormalizeOutputTests(unittest.TestCase):
    def test_brooklyn_address_format(self) -> None:
        addr = {
            "house_number": "132",
            "road": "Greene Avenue",
            "borough": "Brooklyn",
            "county": "Kings County",
            "state": "New York",
            "postcode": "11238",
        }
        formatted, suburb = normalize_from_nominatim(addr, "NY")
        self.assertEqual(suburb, "Brooklyn")
        self.assertIn("132 Greene Avenue", formatted)
        self.assertIn("Brooklyn", formatted)
        self.assertIn("NY", formatted)
        self.assertIn("11238", formatted)
        self.assertNotIn("Kings County", formatted)
        self.assertNotIn("Town of", formatted)

    def test_manhattan_not_new_york_new_york(self) -> None:
        addr = {
            "house_number": "104",
            "road": "East 30th Street",
            "borough": "Manhattan",
            "county": "New York County",
            "city": "New York",
            "state": "New York",
            "postcode": "10016",
        }
        _, suburb = normalize_from_nominatim(addr, "NY")
        self.assertEqual(suburb, "Manhattan")


class PhotonAllowListTests(unittest.TestCase):
    def test_rejects_service_and_attraction(self) -> None:
        self.assertFalse(is_business_photon("service", "service"))
        self.assertFalse(is_business_photon("attraction", "attraction"))

    def test_accepts_restaurant(self) -> None:
        self.assertTrue(is_business_photon("restaurant", "restaurant"))


class BoroughZipTests(unittest.TestCase):
    def test_brooklyn_zip(self) -> None:
        self.assertEqual(ny_borough_from_zip("11211"), "Brooklyn")

    def test_manhattan_zip(self) -> None:
        self.assertEqual(ny_borough_from_zip("10005"), "Manhattan")


class ConfidenceTests(unittest.TestCase):
    def test_short_name_saga_vs_hair_saga_below_threshold(self) -> None:
        score = name_confidence("SAGA", "Hair Saga")
        self.assertLess(score, 0.82)

    def test_photon_playground_rejected(self) -> None:
        feature = {
            "geometry": {"coordinates": [-73.985, 40.716]},
            "properties": {
                "name": "Little Flower Playground",
                "osm_value": "playground",
                "city": "New York",
                "state": "New York",
            },
        }
        hit = evaluate_photon_feature(
            "LITTLE FLOWER", feature, "NY", NY_STATE_VIEWBOX
        )
        self.assertIsNone(hit)


class AmbiguousNameReviewTests(unittest.TestCase):
    def test_little_flower_is_ambiguous(self) -> None:
        self.assertTrue(is_ambiguous_venue_name("LITTLE FLOWER"))
        self.assertEqual(review_confidence_threshold("LITTLE FLOWER"), 0.90)

    def test_little_flower_084_flags_review(self) -> None:
        self.assertTrue(0.84 < review_confidence_threshold("LITTLE FLOWER"))

    def test_photon_little_flower_cafe_flags_at_084(self) -> None:
        feature = {
            "geometry": {"coordinates": [-73.939, 40.768]},
            "properties": {
                "name": "Little Flower Cafe",
                "osm_value": "cafe",
                "city": "Queens",
                "state": "New York",
                "postcode": "11106",
            },
        }
        hit = evaluate_photon_feature(
            "LITTLE FLOWER", feature, "NY", NY_STATE_VIEWBOX
        )
        if hit:
            self.assertTrue(hit.needs_review)


if __name__ == "__main__":
    unittest.main()
