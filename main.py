import cv2
import mediapipe as mp
import numpy as np
import time
import math
from collections import deque

mp_drawing = mp.solutions.drawing_utils
mp_face_mesh = mp.solutions.face_mesh

def euclid(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

def bbox_iou(A, B):
    xA = max(A[0], B[0])
    yA = max(A[1], B[1])
    xB = min(A[2], B[2])
    yB = min(A[3], B[3])
    inter = max(0, xB - xA) * max(0, yB - yA)
    if inter == 0:
        return 0.0
    areaA = (A[2]-A[0]) * (A[3]-A[1])
    areaB = (B[2]-B[0]) * (B[3]-B[1])
    return inter / (areaA + areaB - inter + 1e-8)

POSE_LM_IDX = {"re":33,"le":263,"nt":1,"ml":61,"mr":291,"ch":199}
MODEL_POINTS_3D = np.array([(-30,-30,30),(30,-30,30),(0,0,60),(-25,30,30),(25,30,30),(0,60,10)],dtype=np.float64)

def estimate_pose(lm, shape):
    h,w = shape[:2]
    try:
        pts = []
        for k in ["re","le","nt","ml","mr","ch"]:
            x,y = lm[POSE_LM_IDX[k]]
            pts.append((x,y))
        pts = np.array(pts,dtype=np.float64)
        m = np.array([[w,0,w/2],[0,w,h/2],[0,0,1]],dtype=np.float64)
        ok,r,t = cv2.solvePnP(MODEL_POINTS_3D,pts,m,np.zeros((4,1)))
        if not ok: return None
        R,_ = cv2.Rodrigues(r)
        sy = math.sqrt(R[0,0]**2 + R[1,0]**2)
        if sy < 1e-6:
            x = math.atan2(-R[1,2],R[1,1])
            y = math.atan2(-R[2,0],sy)
            z = 0
        else:
            x = math.atan2(R[2,1],R[2,2])
            y = math.atan2(-R[2,0],sy)
            z = math.atan2(R[1,0],R[0,0])
        return np.degrees(y),np.degrees(x),np.degrees(z)
    except:
        return None

def draw_corner(img,x1,y1,x2,y2,c,t,l):
    cv2.line(img,(x1,y1),(x1+l,y1),c,t)
    cv2.line(img,(x1,y1),(x1,y1+l),c,t)
    cv2.line(img,(x2,y1),(x2-l,y1),c,t)
    cv2.line(img,(x2,y1),(x2,y1+l),c,t)
    cv2.line(img,(x1,y2),(x1+l,y2),c,t)
    cv2.line(img,(x1,y2),(x1,y2-l),c,t)
    cv2.line(img,(x2,y2),(x2-l,y2),c,t)
    cv2.line(img,(x2,y2),(x2,y2-l),c,t)

def draw_scan(frame,x1,y1,x2,y2,s,L,c,t):
    edges=[((x1,y1),(x2,y1),x2-x1),((x2,y1),(x2,y2),y2-y1),((x2,y2),(x1,y2),x2-x1),((x1,y2),(x1,y1),y2-y1)]
    r=s
    for a,b,l in edges:
        if r<=l:
            ax,ay=a;bx,by=b
            sx=int(ax+(bx-ax)*(r/l));sy=int(ay+(by-ay)*(r/l))
            Lm=min(L,l-r)
            ex=int(sx+(bx-ax)*(Lm/l));ey=int(sy+(by-ay)*(Lm/l))
            cv2.line(frame,(sx,sy),(ex,ey),c,2)
            return
        r-=l

def draw_animated_box(f,x1,y1,x2,y2,c,t):
    p=int(8+7*(0.5+0.5*math.sin(t*3)))
    th=2+int(1.5*(0.5+0.5*math.cos(t*2.2)))
    l=int(min(x2-x1,y2-y1)*0.12)+p
    ov=f.copy()
    a=0.12
    for i in range(4,0,-1):
        ci=tuple(min(255,int(ch*(0.6+0.1*i)))for ch in c)
        draw_corner(ov,x1,y1,x2,y2,ci,th+i,l)
        cv2.addWeighted(ov,a,f,1-a,0,f)
        a+=0.03
    draw_corner(f,x1,y1,x2,y2,c,th,l)
    per=2*(x2-x1+y2-y1)
    s=(t*0.5%1)*per
    draw_scan(f,x1,y1,x2,y2,s,40,c,t)

def neon_mesh(f,pts,c):
    pts=[(int(x),int(y))for x,y in pts]
    ov=f.copy()
    for r,a in[(6,0.04),(4,0.03),(2,0.02)]:
        for x,y in pts: cv2.circle(ov,(x,y),r,c,-1,cv2.LINE_AA)
        cv2.addWeighted(ov,a,f,1-a,0,f)
    for x,y in pts: cv2.circle(f,(x,y),1,(255,255,255),-1,cv2.LINE_AA)

class Tracker:
    def __init__(self,i,cent,bbox):
        self.id=i
        self.centroid=cent
        self.bbox=bbox
        self.last=time.time()
        self.landmarks=None

    def update(self,cent,bbox):
        self.centroid=cent
        self.bbox=bbox
        self.last=time.time()

def main():
    cap=cv2.VideoCapture(0)
    fm=mp_face_mesh.FaceMesh(max_num_faces=10,refine_landmarks=True,min_detection_confidence=0.5,min_tracking_confidence=0.5)
    tr=[]
    nid=0
    last=time.time()
    fps=0

    while True:
        ok,frame=cap.read()
        if not ok: break
        frame=cv2.flip(frame,1)
        h,w=frame.shape[:2]
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        res=fm.process(rgb)
        det=[]
        if res.multi_face_landmarks:
            for fl in res.multi_face_landmarks:
                lm=[(int(p.x*w),int(p.y*h))for p in fl.landmark]
                xs=[p[0]for p in lm];ys=[p[1]for p in lm]
                x1,y1=max(0,min(xs)),max(0,min(ys))
                x2,y2=min(w-1,max(xs)),min(h-1,max(ys))
                m=int(0.08*((x2-x1)+(y2-y1)))
                x1=max(0,x1-m);y1=max(0,y1-m)
                x2=min(w-1,x2+m);y2=min(h-1,y2+m)
                cx,cy=(x1+x2)//2,(y1+y2)//2
                det.append(((x1,y1,x2,y2),(cx,cy),lm))

        used=set()
        for bbox,cent,lm in det:
            best=None;bs=-1e9
            for t in tr:
                sc=bbox_iou(bbox,t.bbox)*200 - euclid(cent,t.centroid)*0.8
                if sc>bs: bs=sc;best=t
            if best and bs>-80:
                best.update(cent,bbox);best.landmarks=lm
                used.add(best.id)
            else:
                nt=Tracker(nid,cent,bbox);nt.landmarks=lm
                tr.append(nt);used.add(nid);nid+=1

        tr=[t for t in tr if time.time()-t.last<3.5]

        for t in tr:
            x1,y1,x2,y2=t.bbox
            x1=max(0,x1);y1=max(0,y1);x2=min(w-1,x2);y2=min(h-1,y2)
            c=(50,255,180)
            now=time.time()
            draw_animated_box(frame,x1,y1,x2,y2,c,now)
            if t.landmarks:
                pose=estimate_pose(t.landmarks,frame.shape)
                neon_mesh(frame,t.landmarks,(180,80,255))
                if pose:
                    y,p,r=pose
                    nx,ny=t.landmarks[POSE_LM_IDX["nt"]]
                    cv2.putText(frame,f"{y:+.0f},{p:+.0f},{r:+.0f}",(x1,y1-10),0,0.5,(230,230,230),2)
            cv2.putText(frame,f"ID {t.id}",(x1,y1-25),0,0.7,(255,255,255),2)

        now=time.time()
        fps=0.9*fps+0.1*(1/(now-last+1e-8))
        last=now
        cv2.putText(frame,f"FPS:{int(fps)}",(10,25),0,0.7,(200,200,200),2)
        cv2.imshow("Fancy FaceMesh",frame)
        if cv2.waitKey(1)&0xFF==ord("q"): break

    cap.release()
    cv2.destroyAllWindows()

if __name__=="__main__":
    main()