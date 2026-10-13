"""STEP-specific extraction tests using synthetic straight stock fixtures.

These boxes and channels exercise an evaluator adapter. They are not replacement MARB kit
geometry, benchmark attempts, or evidence of physical load capacity.
"""
from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

try:
    import cadquery as cq
except ImportError:
    cq = None

from grader.fea_step import ROTATIONS, extract_candidate, inspect_stock_section


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(cq is None, "CadQuery runtime unavailable")
class TestFeaStep(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="marb_fea_geometry_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        self.source_file = self.catalog / "stock.step"
        self.stock = cq.Workplane("XY").box(1000, 20, 40).val()
        cq.exporters.export(self.stock, str(self.source_file))
        self.candidate = self.root / "candidate.step"
        cq.exporters.export(self.stock.translate((123, 234, 345)), str(self.candidate))
        self.contract = {
            "schema_version": "marb_fea_contract.v0.1", "task_id": "stock-control", "revision": "v0.1",
            "coverage": "all_solids", "joint_assumption": "rigid", "supports": [], "loads": [],
            "sources": {"beam": self.source()},
        }
        self.bindings = self.bind()

    def source(self):
        return {"file": "stock.step", "sha256": sha(self.source_file), "solid_index": 0,
                "material": {"E_pa": 69e9, "G_pa": 26e9, "nu": 0.33, "rho_kg_m3": 2700, "yield_pa": 276e6},
                "section": {"A_m2": 8e-4, "Iy_m4": 20 * 40**3 / 12 * 1e-12,
                            "Iz_m4": 40 * 20**3 / 12 * 1e-12, "J_m4": 7e-8,
                            "Sy_m3": 20 * 40**2 / 6 * 1e-9, "Sz_m3": 40 * 20**2 / 6 * 1e-9},
                "section_y": [0, 1, 0],
                "stations": [{"node": "left", "point_mm": [-500, 0, 0]},
                             {"node": "center", "point_mm": [0, 0, 0]},
                             {"node": "right", "point_mm": [500, 0, 0]}]}

    def bind(self):
        return {"schema_version": "marb_fea_bindings.v0.1", "candidate_sha256": sha(self.candidate),
                "contract_sha256": "0" * 64,
                "members": {source_id: {"solid_index": index} for index, source_id in enumerate(self.contract["sources"])}}

    def write_candidate(self, shape):
        cq.exporters.export(shape, str(self.candidate))
        self.bindings["candidate_sha256"] = sha(self.candidate)

    def extract(self):
        return extract_candidate(self.contract, self.bindings, self.candidate, self.catalog)

    def channel_control(self):
        # Three analytical rectangles form a C/channel prism. The open section
        # is symmetric about z=20 but its centroid is offset from the y bbox
        # midpoint. This fixture checks geometry and bending properties only.
        channel = (cq.Workplane("YZ")
                   .polyline([(0, 0), (40, 0), (40, 4), (4, 4),
                              (4, 36), (40, 36), (40, 40), (0, 40)])
                   .close().extrude(1000).val())
        area = 4 * 40 + 2 * 36 * 4
        centroid_y = (4 * 40 * 2 + 2 * 36 * 4 * 22) / area
        iy = 4 * 40**3 / 12 + 2 * (36 * 4**3 / 12 + 36 * 4 * 18**2)
        iz = (40 * 4**3 / 12 + 4 * 40 * (2 - centroid_y)**2
              + 2 * (4 * 36**3 / 12 + 36 * 4 * (22 - centroid_y)**2))
        cq.exporters.export(channel, str(self.source_file))
        self.write_candidate(channel.translate((123, 234, 345)))
        source = self.contract["sources"]["beam"]
        source["sha256"] = sha(self.source_file)
        source["section"].update(A_m2=area * 1e-6, Iy_m4=iy * 1e-12, Iz_m4=iz * 1e-12,
                                 Sy_m3=iy / 20 * 1e-9,
                                 Sz_m3=iz / max(centroid_y, 40 - centroid_y) * 1e-9)
        source["stations"] = [{"node": node, "point_mm": [x, centroid_y, 20]}
                              for node, x in (("left", 0), ("center", 500), ("right", 1000))]
        return channel, centroid_y

    def test_has_exactly_twenty_four_proper_rotations(self):
        self.assertEqual(len(ROTATIONS), 24)
        self.assertEqual(len(set(ROTATIONS)), 24)
        for matrix in ROTATIONS:
            self.assertEqual(sum(x * x for row in matrix for x in row), 3)

    def test_translated_candidate_station_geometry_is_used(self):
        frame = self.extract()
        self.assertEqual(len(frame["members"]), 2)
        for actual, expected in zip(frame["nodes"]["left"], [-0.377, 0.234, 0.345]):
            self.assertAlmostEqual(actual, expected, places=9)
        for actual, expected in zip(frame["nodes"]["right"], [0.623, 0.234, 0.345]):
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertEqual(frame["members"][0]["rotation_deg"], 0)
        self.assertEqual(frame["geometry"]["unused_candidate_solids"], 0)

    def test_quarter_turn_changes_candidate_axis_and_section_roll(self):
        self.write_candidate(self.stock.rotate((0, 0, 0), (0, 0, 1), 90).translate((10, 20, 30)))
        frame = self.extract()
        self.assertAlmostEqual(frame["nodes"]["left"][1], -0.480, places=9)
        self.assertAlmostEqual(frame["nodes"]["right"][1], 0.520, places=9)
        self.assertAlmostEqual(frame["members"][0]["rotation_deg"], 0, places=9)

    def test_section_quarter_roll_is_preserved(self):
        self.write_candidate(self.stock.rotate((0, 0, 0), (1, 0, 0), 90))
        frame = self.extract()
        self.assertEqual(abs(frame["members"][0]["rotation_deg"]), 90)

    def test_end_reversal_is_canonicalized_to_positive_stock_direction(self):
        self.write_candidate(self.stock.rotate((0, 0, 0), (0, 1, 0), 180))
        frame = self.extract()
        self.assertLess(frame["nodes"]["left"][0], frame["nodes"]["right"][0])

    def test_arbitrary_angle_candidate_is_not_supported(self):
        self.write_candidate(self.stock.rotate((0, 0, 0), (0, 0, 1), 12))
        with self.assertRaisesRegex(ValueError, "catalog_geometry_mismatch_or_unsupported_rotation"):
            self.extract()

    def test_same_bbox_modified_geometry_is_rejected(self):
        # Two separated half-length stock pieces retain the original bbox.
        shorter = cq.Workplane("XY").box(490, 20, 40).val()
        self.write_candidate(cq.Compound.makeCompound([shorter.translate((-255, 0, 0)), shorter.translate((255, 0, 0))]))
        with self.assertRaisesRegex(ValueError, "candidate_solid_coverage_mismatch"):
            self.extract()

    def test_wrong_area_constants_are_rejected(self):
        self.contract["sources"]["beam"]["section"]["A_m2"] *= 2
        with self.assertRaisesRegex(ValueError, "section_constants_do_not_match_catalog"):
            self.extract()

    def test_same_bbox_and_volume_counterfeit_profile_is_rejected(self):
        # Both are legitimate synthetic linear stock prisms with equal bbox,
        # volume and centroid. Only full BRep congruence distinguishes them.
        bore = cq.Workplane("XY").box(1002, 8, 8).val()
        hollow = self.stock.cut(bore)
        cq.exporters.export(hollow, str(self.source_file))
        small_bore = cq.Workplane("XY").box(1002, 4, 8).val()
        counterfeit = self.stock.cut(small_bore.translate((0, -5, 0))).cut(small_bore.translate((0, 5, 0)))
        self.assertAlmostEqual(counterfeit.Volume(), hollow.Volume(), places=5)
        self.write_candidate(counterfeit)
        source = self.contract["sources"]["beam"]
        source["sha256"] = sha(self.source_file)
        iy = (20 * 40**3 - 8 * 8**3) / 12
        iz = (40 * 20**3 - 8 * 8**3) / 12
        source["section"].update(A_m2=(800 - 64) * 1e-6, Iy_m4=iy * 1e-12,
                                 Iz_m4=iz * 1e-12, Sy_m3=iy / 20 * 1e-9, Sz_m3=iz / 10 * 1e-9)
        with self.assertRaisesRegex(ValueError, "catalog_geometry_mismatch_or_unsupported_rotation"):
            self.extract()

    def test_wrong_axis_inertia_constants_are_rejected(self):
        section = self.contract["sources"]["beam"]["section"]
        section["Iy_m4"], section["Iz_m4"] = section["Iz_m4"], section["Iy_m4"]
        with self.assertRaisesRegex(ValueError, "section_constants_do_not_match_catalog"):
            self.extract()

    def test_candidate_hash_mismatch_is_rejected(self):
        self.bindings["candidate_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "file_hash_mismatch"):
            self.extract()

    def test_source_hash_mismatch_is_rejected(self):
        self.contract["sources"]["beam"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "file_hash_mismatch"):
            self.extract()

    def test_source_index_out_of_bounds_is_rejected(self):
        self.contract["sources"]["beam"]["solid_index"] = 1
        with self.assertRaisesRegex(ValueError, "source_index_out_of_bounds"):
            self.extract()

    def test_candidate_index_out_of_bounds_is_rejected(self):
        self.bindings["members"]["beam"]["solid_index"] = 1
        with self.assertRaisesRegex(ValueError, "candidate_index_out_of_bounds"):
            self.extract()

    def test_candidate_missing_solid_is_rejected(self):
        self.contract["sources"]["beam2"] = copy.deepcopy(self.source())
        self.bindings = self.bind()
        with self.assertRaisesRegex(ValueError, "candidate_solid_coverage_mismatch"):
            self.extract()

    def test_unmapped_candidate_solid_is_rejected(self):
        self.write_candidate(cq.Compound.makeCompound([self.stock, self.stock.translate((0, 100, 0))]))
        with self.assertRaisesRegex(ValueError, "candidate_solid_coverage_mismatch"):
            self.extract()

    def test_duplicate_candidate_mapping_is_rejected(self):
        self.write_candidate(cq.Compound.makeCompound([self.stock, self.stock.translate((0, 100, 0))]))
        self.contract["sources"]["beam2"] = copy.deepcopy(self.source())
        self.bindings = self.bind()
        self.bindings["members"]["beam2"]["solid_index"] = 0
        with self.assertRaisesRegex(ValueError, "duplicate_candidate_mapping"):
            self.extract()

    def test_catalog_path_traversal_is_rejected(self):
        self.contract["sources"]["beam"]["file"] = "../candidate.step"
        with self.assertRaisesRegex(ValueError, "invalid_catalog_path"):
            self.extract()

    def test_station_off_neutral_axis_is_rejected(self):
        self.contract["sources"]["beam"]["stations"][1]["point_mm"][1] = 0.1
        with self.assertRaisesRegex(ValueError, "stations_off_centroidal_stock_axis"):
            self.extract()

    def test_centroidal_asymmetric_channel_stock_is_supported(self):
        _, centroid_y = self.channel_control()
        frame = self.extract()
        self.assertNotAlmostEqual(centroid_y, 20, places=3)
        for node, x in (("left", 123), ("center", 623), ("right", 1123)):
            for actual, expected in zip(frame["nodes"][node], [x / 1000, (234 + centroid_y) / 1000, 0.365]):
                self.assertAlmostEqual(actual, expected, places=9)
        self.assertEqual(frame["members"][0]["rotation_deg"], 0)

    def test_channel_section_inspection_matches_independent_rectangle_sums(self):
        channel, centroid_y = self.channel_control()
        metadata = inspect_stock_section(channel, [0, 1, 0])
        self.assertEqual(metadata["axis"], 0)
        self.assertEqual(metadata["length_mm"], 1000)
        for actual, expected in zip(metadata["centroid_mm"], [500, centroid_y, 20]):
            self.assertAlmostEqual(actual, expected, places=9)
        for key, actual in metadata["section"].items():
            expected = self.contract["sources"]["beam"]["section"][key]
            self.assertAlmostEqual(actual / expected, 1.0, places=8)
        self.assertAlmostEqual(metadata["product_inertia_mm4"], 0, places=6)
        self.assertTrue(metadata["principal_axes_verified"])
        self.assertTrue(metadata["prismatic_verified"])
        self.assertNotIn("J_m4", metadata["section"])
        self.assertNotIn("material", metadata)

    def test_inspection_rejects_nonorthogonal_principal_axis_claim(self):
        with self.assertRaisesRegex(ValueError, "non_orthogonal_section_orientation_unsupported"):
            inspect_stock_section(self.stock, [0, 2**-0.5, 2**-0.5])

    def test_centroidal_channel_axis_and_quarter_roll_follow_candidate(self):
        channel, centroid_y = self.channel_control()
        # Rz(90) * Rx(90): x->y, y->z, z->x. The asymmetric transverse
        # centroid and principal orientation must both follow rigid placement.
        self.write_candidate(channel.rotate((0, 0, 0), (1, 0, 0), 90)
                             .rotate((0, 0, 0), (0, 0, 1), 90).translate((10, 20, 30)))
        frame = self.extract()
        for node, x in (("left", 0), ("center", 500), ("right", 1000)):
            for actual, expected in zip(frame["nodes"][node], [0.030, (x + 20) / 1000, (centroid_y + 30) / 1000]):
                self.assertAlmostEqual(actual, expected, places=9)
        self.assertEqual(abs(frame["members"][0]["rotation_deg"]), 90)

    def test_channel_bbox_centerline_stations_are_rejected(self):
        self.channel_control()
        for station in self.contract["sources"]["beam"]["stations"]:
            station["point_mm"][1] = 20
        with self.assertRaisesRegex(ValueError, "stations_off_centroidal_stock_axis"):
            self.extract()

    def test_channel_bbox_based_section_modulus_is_rejected(self):
        self.channel_control()
        section = self.contract["sources"]["beam"]["section"]
        section["Sz_m3"] = section["Iz_m4"] / 0.020
        with self.assertRaisesRegex(ValueError, "section_constants_do_not_match_catalog"):
            self.extract()

    def test_channel_correct_modulus_requires_the_farther_extreme_fiber(self):
        _, centroid_y = self.channel_control()
        section = self.contract["sources"]["beam"]["section"]
        section["Sz_m3"] = section["Iz_m4"] / (centroid_y / 1000)
        with self.assertRaisesRegex(ValueError, "section_constants_do_not_match_catalog"):
            self.extract()

    def test_channel_nonprismatic_end_cut_is_rejected(self):
        channel, _ = self.channel_control()
        cut = cq.Workplane("XY").box(100, 10, 10).val().translate((950, 2, 20))
        modified = channel.cut(cut)
        cq.exporters.export(modified, str(self.source_file))
        self.write_candidate(modified)
        source = self.contract["sources"]["beam"]
        source["sha256"] = sha(self.source_file)
        center = modified.Center().toTuple()
        for station in source["stations"]:
            station["point_mm"][1:] = list(center[1:])
        with self.assertRaisesRegex(ValueError, "source_not_prismatic_stock"):
            self.extract()

    def test_nonprincipal_asymmetric_stock_bending_axes_are_rejected(self):
        # Unequal rectangular arms make an L profile with a nonzero centroidal
        # y-z product of inertia. Its principal axes need a nonorthogonal roll.
        stock = (cq.Workplane("YZ").polyline([(0, 0), (40, 0), (40, 4),
                                               (4, 4), (4, 30), (0, 30)])
                 .close().extrude(1000).val())
        cq.exporters.export(stock, str(self.source_file))
        self.write_candidate(stock)
        source = self.contract["sources"]["beam"]
        source["sha256"] = sha(self.source_file)
        centroid = stock.Center().toTuple()
        source["stations"] = [{"node": "left", "point_mm": [0, *centroid[1:]]},
                              {"node": "right", "point_mm": [1000, *centroid[1:]]}]
        with self.assertRaisesRegex(ValueError, "non_principal_section_axes_unsupported"):
            self.extract()

    def test_station_not_spanning_stock_is_rejected(self):
        self.contract["sources"]["beam"]["stations"][0]["point_mm"][0] = -400
        with self.assertRaisesRegex(ValueError, "stations_do_not_span_stock"):
            self.extract()

    def test_station_out_of_order_is_rejected(self):
        self.contract["sources"]["beam"]["stations"][1]["point_mm"][0] = -500
        with self.assertRaisesRegex(ValueError, "stations_not_strictly_increasing"):
            self.extract()

    def test_touching_stock_station_joint_closes(self):
        source2 = copy.deepcopy(self.source())
        source2["stations"] = [{"node": "right", "point_mm": [-500, 0, 0]},
                               {"node": "center2", "point_mm": [0, 0, 0]},
                               {"node": "right2", "point_mm": [500, 0, 0]}]
        self.contract["sources"]["beam2"] = source2
        self.write_candidate(cq.Compound.makeCompound([self.stock, self.stock.translate((1000, 0, 0))]))
        self.bindings = self.bind()
        frame = self.extract()
        self.assertEqual(len(frame["nodes"]), 5)
        self.assertAlmostEqual(frame["nodes"]["right"][0], 0.5, places=9)

    def test_disconnected_stock_station_joint_is_rejected(self):
        source2 = copy.deepcopy(self.source())
        source2["stations"] = [{"node": "right", "point_mm": [-500, 0, 0]},
                               {"node": "right2", "point_mm": [500, 0, 0]}]
        self.contract["sources"]["beam2"] = source2
        self.write_candidate(cq.Compound.makeCompound([self.stock, self.stock.translate((1001, 0, 0))]))
        self.bindings = self.bind()
        with self.assertRaisesRegex(ValueError, "joint_stations_do_not_close"):
            self.extract()

    def test_nonprismatic_catalog_stock_is_rejected(self):
        # Linear extrusion along a sloping direction is outside v0.1.
        sloped = self.stock.rotate((0, 0, 0), (0, 0, 1), 5)
        cq.exporters.export(sloped, str(self.source_file))
        self.write_candidate(sloped)
        source = self.contract["sources"]["beam"]
        source["sha256"] = sha(self.source_file)
        bbox = sloped.BoundingBox()
        source["stations"] = [{"node": "left", "point_mm": [bbox.xmin, 0, 0]},
                              {"node": "right", "point_mm": [bbox.xmax, 0, 0]}]
        with self.assertRaisesRegex(ValueError, "source_not_prismatic_stock"):
            self.extract()


if __name__ == "__main__":
    unittest.main()
