//! Maximum-weight closure: the ultimate-pit problem.
//!
//! A pit is a set of blocks that is *closed* under precedence: if a block is
//! mined, every block it depends on (the blocks above it inside the slope
//! cone) is mined too. The ultimate pit is the closed set with the largest
//! total value. Lerchs and Grossmann (1965) solved this with a graph
//! algorithm; Picard (1976) showed it equals a minimum cut, which is what is
//! implemented here (Dinic's maximum-flow algorithm). Both give the same pit.
//!
//! Network: source -> block with capacity `value` for positive blocks,
//! block -> sink with capacity `-value` for negative blocks, and an infinite
//! arc for each precedence `(block, required)`. After the maximum flow, the
//! blocks reachable from the source in the residual network form the
//! *smallest* maximum closure. Smallest closures are nested when the values
//! only grow, which gives nested pit shells for increasing revenue factors.

use std::collections::VecDeque;

use thiserror::Error;

/// Invalid input to [`max_closure`].
#[derive(Debug, Error, PartialEq, Eq)]
pub enum ClosureError {
    /// A precedence arc names a node outside `0..n`.
    #[error("precedence arc {index} refers to node {node}, but there are only {nodes} nodes")]
    BadArc {
        /// Position of the arc in the input.
        index: usize,
        /// The offending node number.
        node: u32,
        /// Number of nodes.
        nodes: usize,
    },
    /// The total of positive values is too large for exact integer flow.
    #[error("sum of positive block values exceeds the exact integer range; scale values down")]
    Overflow,
}

const INF: i64 = i64::MAX / 4;

/// Flow network in compressed adjacency form. Edge `2k` is forward, `2k+1`
/// its residual twin, so the twin of `e` is `e ^ 1` and its tail is
/// `to[e ^ 1]`.
struct Network {
    to: Vec<u32>,
    cap: Vec<i64>,
    start: Vec<usize>,
    adj: Vec<u32>,
}

impl Network {
    fn new(nodes: usize, arcs: &[(u32, u32, i64)]) -> Self {
        let mut to = Vec::with_capacity(arcs.len() * 2);
        let mut cap = Vec::with_capacity(arcs.len() * 2);
        let mut degree = vec![0usize; nodes + 1];
        for &(u, v, c) in arcs {
            to.push(v);
            cap.push(c);
            to.push(u);
            cap.push(0);
            degree[u as usize] += 1;
            degree[v as usize] += 1;
        }
        let mut start = vec![0usize; nodes + 1];
        for i in 0..nodes {
            start[i + 1] = start[i] + degree[i];
        }
        let mut fill = start.clone();
        let mut adj = vec![0u32; arcs.len() * 2];
        for (k, &(u, v, _)) in arcs.iter().enumerate() {
            let e = u32::try_from(2 * k).expect("edge count fits u32");
            adj[fill[u as usize]] = e;
            fill[u as usize] += 1;
            adj[fill[v as usize]] = e + 1;
            fill[v as usize] += 1;
        }
        Self {
            to,
            cap,
            start,
            adj,
        }
    }

    fn edges(&self, u: usize) -> &[u32] {
        &self.adj[self.start[u]..self.start[u + 1]]
    }

    fn levels(&self, s: usize, level: &mut [i32]) {
        level.fill(-1);
        level[s] = 0;
        let mut queue = VecDeque::from([s]);
        while let Some(u) = queue.pop_front() {
            for &e in self.edges(u) {
                let v = self.to[e as usize] as usize;
                if self.cap[e as usize] > 0 && level[v] < 0 {
                    level[v] = level[u] + 1;
                    queue.push_back(v);
                }
            }
        }
    }

    /// Dinic's algorithm with an iterative depth-first search.
    #[allow(clippy::many_single_char_names)] // s, t, u, v, e: graph notation
    fn max_flow(&mut self, s: usize, t: usize) {
        let n = self.start.len() - 1;
        let mut level = vec![-1i32; n];
        let mut next = vec![0usize; n];
        let mut path: Vec<u32> = Vec::new();
        loop {
            self.levels(s, &mut level);
            if level[t] < 0 {
                return;
            }
            for (u, slot) in next.iter_mut().enumerate() {
                *slot = self.start[u];
            }
            path.clear();
            let mut u = s;
            loop {
                if u == t {
                    let push = path
                        .iter()
                        .map(|&e| self.cap[e as usize])
                        .min()
                        .unwrap_or(0);
                    let mut retreat = path.len();
                    for (i, &e) in path.iter().enumerate() {
                        self.cap[e as usize] -= push;
                        self.cap[(e ^ 1) as usize] += push;
                        if self.cap[e as usize] == 0 && retreat == path.len() {
                            retreat = i;
                        }
                    }
                    path.truncate(retreat);
                    u = path.last().map_or(s, |&e| self.to[e as usize] as usize);
                    continue;
                }
                let mut advanced = false;
                while next[u] < self.start[u + 1] {
                    let e = self.adj[next[u]] as usize;
                    let v = self.to[e] as usize;
                    if self.cap[e] > 0 && level[v] == level[u] + 1 {
                        path.push(u32::try_from(e).expect("edge id fits u32"));
                        u = v;
                        advanced = true;
                        break;
                    }
                    next[u] += 1;
                }
                if advanced {
                    continue;
                }
                // Dead end: remove u from the level graph and step back.
                level[u] = -1;
                match path.pop() {
                    None => break,
                    Some(e) => {
                        u = self.to[(e ^ 1) as usize] as usize;
                        next[u] += 1;
                    }
                }
            }
        }
    }

    fn reachable(&self, s: usize) -> Vec<bool> {
        let mut seen = vec![false; self.start.len() - 1];
        seen[s] = true;
        let mut stack = vec![s];
        while let Some(u) = stack.pop() {
            for &e in self.edges(u) {
                let v = self.to[e as usize] as usize;
                if self.cap[e as usize] > 0 && !seen[v] {
                    seen[v] = true;
                    stack.push(v);
                }
            }
        }
        seen
    }
}

/// Smallest maximum-weight closed set.
///
/// `values[i]` is the value of node `i` (e.g. block value in cents).
/// `precedence` holds pairs `(i, j)`: taking `i` requires taking `j`.
/// Returns one flag per node, `true` for nodes in the closure.
///
/// # Errors
/// [`ClosureError::BadArc`] for an arc outside `0..n`;
/// [`ClosureError::Overflow`] if the positive values sum to more than
/// `i64::MAX / 4`.
pub fn max_closure(values: &[i64], precedence: &[(u32, u32)]) -> Result<Vec<bool>, ClosureError> {
    let n = values.len();
    for (index, &(a, b)) in precedence.iter().enumerate() {
        for node in [a, b] {
            if node as usize >= n {
                return Err(ClosureError::BadArc {
                    index,
                    node,
                    nodes: n,
                });
            }
        }
    }
    let mut positive: i64 = 0;
    for &v in values {
        if v > 0 {
            positive = positive
                .checked_add(v)
                .filter(|p| *p < INF)
                .ok_or(ClosureError::Overflow)?;
        }
    }
    let s = u32::try_from(n).map_err(|_| ClosureError::Overflow)?;
    let t = s + 1;
    let mut arcs: Vec<(u32, u32, i64)> = Vec::with_capacity(n + precedence.len());
    for (i, &v) in values.iter().enumerate() {
        let i = u32::try_from(i).map_err(|_| ClosureError::Overflow)?;
        if v > 0 {
            arcs.push((s, i, v));
        } else if v < 0 {
            arcs.push((i, t, -v));
        }
    }
    arcs.extend(precedence.iter().map(|&(a, b)| (a, b, INF)));
    let mut net = Network::new(n + 2, &arcs);
    net.max_flow(s as usize, t as usize);
    let mut closure = net.reachable(s as usize);
    closure.truncate(n);
    Ok(closure)
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn closed(set: &[bool], precedence: &[(u32, u32)]) -> bool {
        precedence
            .iter()
            .all(|&(a, b)| !set[a as usize] || set[b as usize])
    }

    fn total(set: &[bool], values: &[i64]) -> i64 {
        set.iter()
            .zip(values)
            .filter(|(s, _)| **s)
            .map(|(_, v)| v)
            .sum()
    }

    /// Exhaustive optimum over all closed subsets (small n only).
    fn brute(values: &[i64], precedence: &[(u32, u32)]) -> i64 {
        let n = values.len();
        let mut best = 0;
        for mask in 0u32..(1 << n) {
            let set: Vec<bool> = (0..n).map(|i| mask & (1 << i) != 0).collect();
            if closed(&set, precedence) {
                best = best.max(total(&set, values));
            }
        }
        best
    }

    #[test]
    fn two_dimensional_textbook_section() {
        // 3 benches x 5 columns, 45 degree slopes with square blocks: a block
        // needs the three blocks above it. Row 0 is the top bench.
        let values = [
            -1, -1, -1, -1, -1, //
            -1, -1, 5, -1, -1, //
            -2, -2, 10, -2, -2,
        ];
        let mut precedence = Vec::new();
        for r in 1..3u32 {
            for c in 0..5u32 {
                for dc in [-1i32, 0, 1] {
                    let cc = c.cast_signed() + dc;
                    if (0..5).contains(&cc) {
                        precedence.push((r * 5 + c, (r - 1) * 5 + cc.cast_unsigned()));
                    }
                }
            }
        }
        let pit = max_closure(&values, &precedence).unwrap();
        // The 10 needs (-1, 5, -1) on the middle bench and all five top blocks:
        // 10 + 3 - 5 = 8.
        assert_eq!(total(&pit, &values), 8);
        assert_eq!(brute(&values, &precedence), 8);
        assert!(closed(&pit, &precedence));
    }

    #[test]
    fn nothing_worth_mining_gives_empty_pit() {
        let pit = max_closure(&[-3, 2, -5], &[(1, 0), (1, 2)]).unwrap();
        assert_eq!(pit, vec![false, false, false]);
    }

    #[test]
    fn zero_value_blocks_are_left_out() {
        // Smallest maximum closure: ties are not mined.
        let pit = max_closure(&[0, 3, -3], &[(1, 2)]).unwrap();
        assert_eq!(pit, vec![false, false, false]);
    }

    #[test]
    fn bad_arc_and_overflow_are_reported() {
        assert!(matches!(
            max_closure(&[1, 2], &[(0, 5)]),
            Err(ClosureError::BadArc { node: 5, .. })
        ));
        assert_eq!(
            max_closure(&[i64::MAX / 3, i64::MAX / 3], &[]),
            Err(ClosureError::Overflow)
        );
    }

    fn graph() -> impl Strategy<Value = (Vec<i64>, Vec<(u32, u32)>)> {
        (2usize..=11).prop_flat_map(|n| {
            let nodes = u32::try_from(n).unwrap();
            (
                prop::collection::vec(-20i64..=20, n),
                prop::collection::vec((0..nodes, 0..nodes), 0..=3 * n),
            )
        })
    }

    proptest! {
        #[test]
        fn matches_exhaustive_search((values, precedence) in graph()) {
            let pit = max_closure(&values, &precedence).unwrap();
            prop_assert!(closed(&pit, &precedence));
            prop_assert_eq!(total(&pit, &values), brute(&values, &precedence));
        }

        #[test]
        fn shells_are_nested((values, precedence) in graph(), extra in 1i64..10) {
            let small = max_closure(&values, &precedence).unwrap();
            let more: Vec<i64> = values.iter().map(|v| v + extra).collect();
            let large = max_closure(&more, &precedence).unwrap();
            for (a, b) in small.iter().zip(&large) {
                prop_assert!(!*a || *b);
            }
        }
    }
}
