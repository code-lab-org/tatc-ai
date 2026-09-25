from tatc.schemas import WalkerConstellation, TwoLineElements, Instrument, Point
from tatc.utils import swath_width_to_field_of_regard
from tatc.generation import generate_points_uniform_spacing
from tatc.analysis import collect_multi_observations, aggregate_observations, reduce_observations
from datetime import timedelta
import pandas as pd

orbit = TwoLineElements(tle=[
    "1 43013U 17073A   26022.94240312  .00000200  00000+0  11574-3 0  9998",
    "2 43013  98.7645 323.7982 0001179  28.0279 332.0961 14.19539020423863"])
altitude_m = orbit.get_altitude(); swath_width_m = 3000e3
instrument = Instrument(name="VIIRS", field_of_regard=swath_width_to_field_of_regard(altitude_m, swath_width_m))
constellation = WalkerConstellation(name="NOAA 20", orbit=orbit, instruments=[instrument],
    configuration="delta", number_satellites=3, number_planes=3)

points_df = generate_points_uniform_spacing(5000e3)
points = points_df.apply(lambda r: Point(id=r.point_id, latitude=r.geometry.y, longitude=r.geometry.x), axis=1)
start = orbit.get_epoch(); end = start + timedelta(days=30)
raw = pd.concat([collect_multi_observations(p, constellation.generate_members(), start, end) for p in points])
reduced = reduce_observations(aggregate_observations(raw))
reduced["mean_revisit_hr"] = reduced.apply(lambda r: r["revisit"] / timedelta(hours=1), axis=1)
RESULT = [{"point_id": int(r.point_id), "lon": float(r.geometry.x), "lat": float(r.geometry.y),
           "mean_revisit_hr": float(r.mean_revisit_hr)} for r in reduced.itertuples()]
