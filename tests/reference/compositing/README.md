# T-012 — MineCore compositing reference cases

## Scope and conventions

This reference set exercises fixed-length, domain-honouring, and bench compositing. Depths are metres along the hole; intervals are half-open `[from, to)`. Grades are length-weighted, not density-weighted. A null grade is missing data, never zero. `sampled_length` counts only overlaps with non-null grades; `coverage = sampled_length / composite interval length`. If coverage is below `min_coverage`, the composite remains in the output but its grade is null.

Input samples are sorted by depth for validation. Any overlap between input intervals rejects the whole case. Touching endpoints are allowed.

### Implemented behavior

- **Fixed-length:** bins are anchored at `origin + k*L`. The first bin is the one containing the shallowest sample depth; output is clipped at the deepest input depth for the final partial bin.
- **Last partial bin:** `drop` removes a final bin shorter than `L/2`; `keep` retains it at its true length; `merge` combines it with the preceding bin when the final bin is shorter than `L/2`.
- **Domain-honouring:** same-domain consecutive runs are composited independently, each from its first sample depth. A domain change starts a new run even if a gap separates the runs. A one-sample domain is retained.
- **Bench:** plane elevations are `bench_base + k*H`. With `z(depth)=z0+depth*sin(dip)`, each plane crossing is converted to a depth and used as a boundary. For a vertical hole, this convention is represented by `dip_deg=-90` (depth increases downward).
- **Rejected cases:** invalid/overlapping sample intervals are reported as a rejected case, with no composite rows.

### Cases included

| Case ID | Intent |
|---|---|
| C01_regular_1m | Regular 1 m samples, L=2. |
| C02_irregular | Irregular lengths spanning 0.3–2.7 m, L=2.5. |
| C03_gap_middle | A 1 m gap in the middle. |
| C04_missing_grade | Null grade contributes neither grade numerator nor sampled length. |
| C05_low_coverage | Coverage below threshold; composite retained with null grade. |
| C06_last_drop | Final 1 m partial composite is dropped. |
| C07_last_keep | Final 1 m partial composite retained with true length. |
| C08_last_merge | Final 0.5 m partial composite merged into previous composite. |
| C09_nonzero_origin | Composite origin is 1 m rather than zero. |
| C10_boundary_sample | Composite boundary exactly equals a sample boundary. |
| C11_sample_spans_three | One 3 m sample spans three 1 m composites. |
| C12_all_null | A composite with only null grades has null grade and zero sampled length. |
| C13_domain_three | Three domains, including a one-sample short domain; restart at boundaries. |
| C14_domain_change_gap | Domain changes across a gap and starts a new run. |
| C15_bench_vertical | Vertical hole (dip -90°), H=10; bench planes split depth intervals. |
| C16_bench_inclined | Inclined hole dip -60°, H=5, nonzero bench_base. |
| C17_overlap_rejected | Input samples overlap; case must be rejected. |

## Independent verification method

The reference outputs use analytic interval arithmetic: each sample/composite overlap is `max(0, min(comp_to, sample_to) - max(comp_from, sample_from))`. Grade numerator and sampled length are accumulated only for non-null grades.

The second implementation uses midpoint numerical quadrature. It inserts every sample endpoint that falls inside the composite as an exact segment boundary, then subdivides each segment into cells no longer than `0.0001 m` and evaluates the sample at each cell midpoint. This breakpoint-aware grid avoids assigning a cell across a discontinuity. Because endpoints are exact breakpoints, this is a fine-grid numerical integration check, not a claim that all cells are aligned to a single global depth-zero grid.

Acceptance thresholds: grade disagreement ≤ `1e-6`; sampled-length disagreement ≤ `1e-4 m`. Values in the CSV are written to 9 decimal places. The overlap case is expected to be rejected and is not included as a numeric agreement test.

## Agreement table

```text
case_id,status,max_grade_disagreement,max_sampled_length_disagreement
C01_regular_1m,PASS,4.17443857259e-13,2.03836947321e-13
C02_irregular,PASS,2.54996024296e-12,1.74082970261e-12
C03_gap_middle,PASS,4.17443857259e-13,2.03836947321e-13
C04_missing_grade,PASS,7.53175299906e-13,2.03836947321e-13
C05_low_coverage,PASS,0,9.38138455808e-14
C06_last_drop,PASS,7.62057084103e-13,2.03836947321e-13
C07_last_keep,PASS,7.62057084103e-13,2.03836947321e-13
C08_last_merge,PASS,5.3423931945e-13,8.51319015283e-13
C09_nonzero_origin,PASS,7.53175299906e-13,2.03836947321e-13
C10_boundary_sample,PASS,8.74855743405e-13,2.03836947321e-13
C11_sample_spans_three,PASS,1.59960933388e-12,9.38138455808e-14
C12_all_null,PASS,0,2.03836947321e-13
C13_domain_three,PASS,6.89226453687e-13,2.03836947321e-13
C14_domain_change_gap,PASS,7.95807864051e-13,2.03836947321e-13
C15_bench_vertical,PASS,3.5482727867e-12,9.96713822587e-12
C16_bench_inclined,PASS,9.92628201857e-12,7.74225128453e-12
C17_overlap_rejected,REJECTED,0,0
```

All non-rejected cases passed the stated tolerances. The overlap case was rejected as expected. Numerical values above are from running the included code in this environment.

## Reproduction

Save the following two files in one directory: `cases.json` (the case definitions distributed with this task) and `verify_compositing.py` (the code below), then run:

```bash
python verify_compositing.py cases.json
```

The script writes `cases.csv` in the working directory and prints the agreement table.

## Full verification code

```python
import csv, json, math
from pathlib import Path

GRID = 0.0001
TOL_GRADE = 1e-6
TOL_LENGTH = 1e-4

def validate(samples):
    s = sorted(samples, key=lambda x: (x["from"], x["to"]))
    for a, b in zip(s, s[1:]):
        if b["from"] < a["to"] - 1e-12:
            raise ValueError("overlapping input samples")
    for x in s:
        if x["to"] <= x["from"]:
            raise ValueError("non-positive sample interval")
    return s

def stats(samples, a, b, min_coverage=0.5, domain=None):
    length = b-a
    sampled = weighted = 0.0
    for s in samples:
        if domain is not None and s.get("domain") != domain:
            continue
        ov = max(0.0, min(b, s["to"])-max(a, s["from"]))
        if ov and s.get("grade") is not None:
            sampled += ov
            weighted += ov*s["grade"]
    cov = sampled/length if length else 0.0
    grade = weighted/sampled if sampled and cov + 1e-12 >= min_coverage else None
    return grade, sampled, cov

def fixed(samples, L, origin=0.0, min_coverage=0.5, last_mode="keep"):
    samples=validate(samples)
    if not samples: return []
    lo=min(x["from"] for x in samples); hi=max(x["to"] for x in samples)
    k=math.floor((lo-origin)/L)
    a=origin+k*L
    while a+L <= lo+1e-12: a+=L
    # Keep full bins from first intersecting bin through end, clipping only last bin.
    bins=[]
    a=origin+math.floor((lo-origin)/L)*L
    while a < hi-1e-12:
        b=a+L
        end=min(b,hi)
        if end>a+1e-12:
            bins.append([a,end])
        a=b
    if bins and bins[-1][1]-bins[-1][0] < L/2-1e-12:
        if last_mode=="drop":
            bins.pop()
        elif last_mode=="merge" and len(bins)>1:
            bins[-2][1]=bins[-1][1]; bins.pop()
        # keep retains actual end
    out=[]
    for a,b in bins:
        g,sl,c=stats(samples,a,b,min_coverage)
        out.append(dict(comp_from=a,comp_to=b,grade=g,sampled_length=sl,coverage=c,domain=""))
    return out

def domain_composite(samples, L, min_coverage=0.5):
    samples=validate(samples)
    # Consecutive input rows with same domain form runs; any domain change restarts,
    # including across a depth gap. Each run is composited from its first depth.
    out=[]
    for dom in dict.fromkeys(s.get("domain","") for s in samples):
        runs=[]; current=[]
        for s in samples:
            if s.get("domain","")==dom:
                if current and abs(current[-1]["to"]-s["from"])>1e-12:
                    runs.append(current); current=[]
                current.append(s)
            elif current:
                runs.append(current); current=[]
        if current: runs.append(current)
        for run in runs:
            # Domain runs in this task are processed in original depth order.
            out.extend(fixed(run,L,origin=run[0]["from"],min_coverage=min_coverage,last_mode="keep"))
            for row in out[-len(fixed(run,L,origin=run[0]["from"],min_coverage=min_coverage,last_mode="keep")):]:
                row["domain"]=dom
    return sorted(out,key=lambda r:(r["comp_from"],r["comp_to"]))

def bench_bins(samples, H, bench_base, z0, dip_deg, min_coverage=0.5):
    samples=validate(samples)
    if not samples: return []
    slope=math.sin(math.radians(dip_deg))
    if abs(slope)<1e-12:
        # Vertical hole: depth convention is z=z0; horizontal plane crossings
        # are not depth-resolvable unless vertical direction is represented by dip=+/-90.
        raise ValueError("dip must be +/-90 degrees for a vertical hole under z=z0+depth*sin(dip)")
    lo=min(s["from"] for s in samples); hi=max(s["to"] for s in samples)
    zlo=z0+lo*slope; zhi=z0+hi*slope
    zmin,zmax=min(zlo,zhi),max(zlo,zhi)
    k0=math.floor((zmin-bench_base)/H)-1
    planes=[]
    k=k0
    while bench_base+k*H <= zmax+H:
        z=bench_base+k*H
        d=(z-z0)/slope
        if lo+1e-12 < d < hi-1e-12: planes.append(d)
        k+=1
    edges=sorted([lo]+planes+[hi])
    out=[]
    for a,b in zip(edges,edges[1:]):
        g,sl,c=stats(samples,a,b,min_coverage)
        out.append(dict(comp_from=a,comp_to=b,grade=g,sampled_length=sl,coverage=c,domain=""))
    return out

def numeric_stats(samples, a, b, min_coverage=0.5, domain=None):
    # Midpoint quadrature on a 0.1-mm grid, clipped to composite bounds.
    # Exact interval endpoints are included as breakpoints so no interval is
    # silently assigned to the wrong sample; partial edge cells are integrated exactly.
    edges={a,b}
    for s in samples:
        if a < s["from"] < b: edges.add(s["from"])
        if a < s["to"] < b: edges.add(s["to"])
    # To implement the required fixed grid while respecting endpoints, use grid
    # cells within each breakpoint segment and midpoint classification.
    pts=sorted(edges); sampled=weighted=0.0
    for x,y in zip(pts,pts[1:]):
        n=max(1, math.ceil((y-x)/GRID-1e-12))
        dx=(y-x)/n
        for i in range(n):
            mid=x+(i+0.5)*dx
            for s in samples:
                if domain is not None and s.get("domain")!=domain: continue
                if s["from"] <= mid < s["to"] and s.get("grade") is not None:
                    sampled += dx; weighted += dx*s["grade"]; break
    length=b-a; cov=sampled/length if length else 0
    grade=weighted/sampled if sampled and cov+1e-12>=min_coverage else None
    return grade,sampled,cov

def run_case(case):
    mode=case["mode"]; s=case["samples"]; p=case["params"]
    try:
        if mode=="fixed":
            rows=fixed(s,p["L"],p.get("origin",0),p.get("min_coverage",0.5),p.get("last_mode","keep"))
        elif mode=="domain":
            rows=domain_composite(s,p["L"],p.get("min_coverage",0.5))
        elif mode=="bench":
            rows=bench_bins(s,p["H"],p["bench_base"],p["z0"],p["dip_deg"],p.get("min_coverage",0.5))
        else: raise ValueError("unknown mode")
        return rows, None
    except ValueError as e:
        return [], str(e)

def main():
    import sys
    source=Path(sys.argv[1]) if len(sys.argv)>1 else Path("cases.json")
    cases=json.loads(source.read_text())
    csv_path=Path("cases.csv")
    agreement=[]
    with csv_path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["case_id","mode","params_json","samples_json","comp_from","comp_to","grade","sampled_length","coverage","domain","note"])
        w.writeheader()
        for c in cases:
            rows,error=run_case(c)
            if error:
                w.writerow(dict(case_id=c["case_id"],mode=c["mode"],params_json=json.dumps(c["params"],sort_keys=True),samples_json=json.dumps(c["samples"],separators=(",",":")),comp_from="",comp_to="",grade="",sampled_length="",coverage="",domain="",note="REJECTED: "+error))
                agreement.append((c["case_id"],"REJECTED",0,0))
                continue
            max_g=max_l=0.0
            for r in rows:
                ng,nsl,ncov=numeric_stats(c["samples"],r["comp_from"],r["comp_to"],c["params"].get("min_coverage",0.5),r["domain"] or None)
                if r["grade"] is not None and ng is not None: max_g=max(max_g,abs(r["grade"]-ng))
                max_l=max(max_l,abs(r["sampled_length"]-nsl))
                w.writerow(dict(case_id=c["case_id"],mode=c["mode"],params_json=json.dumps(c["params"],sort_keys=True,separators=(",",":")),samples_json=json.dumps(c["samples"],separators=(",",":")),comp_from=f'{r["comp_from"]:.9f}',comp_to=f'{r["comp_to"]:.9f}',grade="" if r["grade"] is None else f'{r["grade"]:.9f}',sampled_length=f'{r["sampled_length"]:.9f}',coverage=f'{r["coverage"]:.9f}',domain=r["domain"],note=c["note"]))
            agreement.append((c["case_id"],"PASS" if max_g<=TOL_GRADE and max_l<=TOL_LENGTH else "FAIL",max_g,max_l))
    print("case_id,status,max_grade_disagreement,max_sampled_length_disagreement")
    for x in agreement: print(f"{x[0]},{x[1]},{x[2]:.12g},{x[3]:.12g}")
    if any(x[1]=="FAIL" for x in agreement): raise SystemExit(1)

if __name__=="__main__": main()
```

## Architect review (T-012)

Received 2026-10-25 from an independent verifier and kept byte-for-byte above.
The architect recomputed every non-rejected case with a third implementation
written from the task statement alone (not from the code above): all 16 cases
agree, worst difference 1.7e-10. C17 is correctly rejected.

Semantics these references pin down, binding for the M2 kernel:

- Fixed-length bins are anchored at `origin + kL`; the first bin is the one
  containing the shallowest sample, and the last bin is clipped at the deepest
  sample end.
- `merge` joins a final bin shorter than L/2 to the previous bin; `drop`
  removes it; a final bin of exactly L/2 is kept in every mode.
- The coverage test is `coverage >= min_coverage`.
- Domain runs are consecutive same-domain samples; each run restarts at its
  own first depth, also across a gap.
