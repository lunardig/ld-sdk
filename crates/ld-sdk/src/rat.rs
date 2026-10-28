//! Exact rationals over `i128`, always reduced with a positive denominator. Overflow panics
//! rather than wrapping; callers turn panics at the host boundary into `ENGINE_FAULT`.

use std::cmp::Ordering;
use std::fmt;

use crate::canonical::{int, positive, to_i128, to_u128};
use crate::types::Rational;

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct Rat {
    n: i128,
    d: i128,
}

fn gcd(mut a: i128, mut b: i128) -> i128 {
    a = a.abs();
    b = b.abs();
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a.max(1)
}

// Named methods instead of operator traits: every call site reads as exact arithmetic,
// and overflow panics (ENGINE_FAULT) instead of hiding behind `*` and `/`.
#[allow(clippy::should_implement_trait)]
impl Rat {
    pub const ZERO: Rat = Rat { n: 0, d: 1 };
    pub const ONE: Rat = Rat { n: 1, d: 1 };

    pub fn new(n: i128, d: i128) -> Rat {
        assert!(d != 0, "zero denominator");
        let (n, d) = if d < 0 { (-n, -d) } else { (n, d) };
        let g = gcd(n, d);
        Rat { n: n / g, d: d / g }
    }
    pub fn int(n: i128) -> Rat {
        Rat { n, d: 1 }
    }
    pub fn numer(self) -> i128 {
        self.n
    }
    pub fn denom(self) -> i128 {
        self.d
    }
    pub fn is_zero(self) -> bool {
        self.n == 0
    }
    pub fn add(self, o: Rat) -> Rat {
        Rat::new(
            self.n.checked_mul(o.d).and_then(|a| o.n.checked_mul(self.d).and_then(|b| a.checked_add(b))).expect("rational overflow"),
            self.d.checked_mul(o.d).expect("rational overflow"),
        )
    }
    pub fn sub(self, o: Rat) -> Rat {
        self.add(Rat { n: -o.n, d: o.d })
    }
    pub fn mul(self, o: Rat) -> Rat {
        let g1 = gcd(self.n, o.d);
        let g2 = gcd(o.n, self.d);
        Rat::new(
            (self.n / g1).checked_mul(o.n / g2).expect("rational overflow"),
            (self.d / g2).checked_mul(o.d / g1).expect("rational overflow"),
        )
    }
    pub fn div(self, o: Rat) -> Rat {
        assert!(o.n != 0, "division by zero");
        self.mul(Rat::new(o.d, o.n))
    }
    /// Largest integer not greater than self.
    pub fn floor(self) -> i128 {
        self.n.div_euclid(self.d)
    }
    pub fn min(self, o: Rat) -> Rat {
        if self <= o { self } else { o }
    }
    pub fn max(self, o: Rat) -> Rat {
        if self >= o { self } else { o }
    }
    pub fn to_rational(self) -> Rational {
        Rational { n: int(self.n), d: positive(self.d as u128) }
    }
}

impl From<&Rational> for Rat {
    fn from(r: &Rational) -> Rat {
        Rat::new(to_i128(&r.n), to_u128(&r.d) as i128)
    }
}

impl Ord for Rat {
    fn cmp(&self, o: &Rat) -> Ordering {
        (self.n * o.d).cmp(&(o.n * self.d))
    }
}
impl PartialOrd for Rat {
    fn partial_cmp(&self, o: &Rat) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl fmt::Debug for Rat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}/{}", self.n, self.d)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reduces_and_floors() {
        assert_eq!(Rat::new(6, -4), Rat::new(-3, 2));
        assert_eq!(Rat::new(-3, 2).floor(), -2);
        assert_eq!(Rat::new(7, 3).floor(), 2);
        assert_eq!(Rat::new(1, 3).add(Rat::new(1, 6)), Rat::new(1, 2));
        assert_eq!(Rat::new(2, 3).mul(Rat::new(9, 4)), Rat::new(3, 2));
    }
}
