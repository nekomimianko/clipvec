import sys, math; sys.path.insert(0, '.')
from clipvec.document import ClipDocument
from clipvec.vector import Stroke, Point, SPLINE, NORMAL, FREEHAND, CLOSED, STRAIGHT
doc = ClipDocument.load('samples/t07_spline.clip')
L = doc.vector_layers()[0]
S = []
# S0 sine with pressure 0->1->0, red, radius 6
pts = [Point(100 + i * 20, 200 + 100 * math.sin(i / 40 * 2 * math.pi), pressure=math.sin(i / 40 * math.pi)) for i in range(41)]
S.append(Stroke(pts, color=(230, 30, 30), radius=6.0, brush_id=1))
# S1 closed circle spline green
pts = [Point(300 + 150 * math.cos(a / 16 * 2 * math.pi), 550 + 150 * math.sin(a / 16 * 2 * math.pi), pressure=1.0) for a in range(16)]
S.append(Stroke(pts, flags=NORMAL | SPLINE | CLOSED, color=(20, 160, 60), radius=4.0))
# S2 zigzag with corners, straight segments via corner flags, blue
zz = [(600, 400), (700, 700), (800, 400), (900, 700)]
pts = [Point(x, y, pressure=0.8, corner=True) for x, y in zz]
S.append(Stroke(pts, color=(30, 60, 220), radius=5.0))
# S3 opacity factor ramp black, stroke opacity .8
pts = [Point(100 + i * 40, 900, pressure=1.0, opacity=i / 20) for i in range(21)]
S.append(Stroke(pts, color=(0, 0, 0), radius=8.0, opacity=0.8))
# S4 width factor ramp, sparse 3 points spline
pts = [Point(150, 750, width=0.2), Point(300, 800, width=1.0), Point(450, 750, width=0.2)]
S.append(Stroke(pts, color=(200, 0, 200), radius=8.0))
doc.set_strokes(L.layer_id, S)
doc.save('samples/w01_synthetic.clip')
print('saved')
