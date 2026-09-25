from tatc.schemas import WalkerConstellation, TwoLineElements, Instrument, Point
from tatc.utils import swath_width_to_field_of_regard
from tatc.analysis import collect_multi_observations, aggregate_observations, reduce_observations
from datetime import timedelta

orbit = TwoLineElements(tle=[
    "1 43013U 17073A   26022.94240312  .00000200  00000+0  11574-3 0  9998",
    "2 43013  98.7645 323.7982 0001179  28.0279 332.0961 14.19539020423863"])
altitude_m = orbit.get_altitude(); swath_width_m = 3000e3
instrument = Instrument(name="VIIRS", field_of_regard=swath_width_to_field_of_regard(altitude_m, swath_width_m))
point = Point(id=0, latitude=33.4255, longitude=-111.9400)
start = orbit.get_epoch(); end = start + timedelta(days=30)

RESULT = []
for x in range(1, 7):
    c = WalkerConstellation(name="NOAA 20", orbit=orbit, instruments=[instrument],
        configuration="delta", number_satellites=3*x, number_planes=3)
    reduced = reduce_observations(aggregate_observations(
        collect_multi_observations(point, c.generate_members(), start, end)))
    RESULT.append(float(reduced.iloc[0].revisit / timedelta(hours=1)))
