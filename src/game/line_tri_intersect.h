#ifndef _LINE_TRI_INTERSECT_H_
#define _LINE_TRI_INTERSECT_H_
#include <ultra64.h>
#include <bondtypes.h>
bool intersectRayTriangle(Vertex *vertex0, Vertex *vertex1, Vertex *vertex2, coord3d *vertexOffset, coord3d *rayStart, coord3d *linePoint, coord3d *rayDirection, HitThing *hit);

#endif
