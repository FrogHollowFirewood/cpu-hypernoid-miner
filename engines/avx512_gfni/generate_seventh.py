#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the 64-lane Poseidon2b S-box for the GNU x86-64 ABI.

Each virtual vector is one AES-field coefficient across 64 nonces.
The generator builds the tower arithmetic first, then uses future operand
uses to assign the vectors to registers or aligned stack slots.
"""

from collections import Counter, defaultdict, deque
from pathlib import Path
import re

from generate import apply_basis
from generate_aes_tables import load_array
from pathlib import Path
import re

from generate_aes_tables import aes_product

XOR_SEARCH = 60
TERNARY_SEARCH = 36

MUL_DIRECTIONS = ((1, 1, 1), (1, 1, 1), (1, 1, 1))
OUTPUT_DIRECTIONS = (7, 7, 7)
EXTRA_COEFFS = [77, 250, 133, 241, 236, 134, 105, 119, 24, 34, 206, 191, 214, 251]

MEMORY_BIAS = 24
SQUARE_AS_MUL = True
MATRIX_ALIAS = 1
MEMORY_MATRIX_MASK = 896


# Low, high and sum product order for each tower level, from byte pairs
# upward. Each S-box product has a separate schedule to shorten live ranges.
NORM_ORDER = ('slh', 'lsh', 'lsh')
CORRECTION_ORDER = ('slh', 'lsh', 'lsh')
TRACE_ORDER = ('hsl', 'hls', 'slh')
OUTPUT_ORDER = ('hsl', 'slh', 'hsl')

# An imminent second use can amortize an explicit register load.
RELOAD_WINDOW = 3


class TowerProgram:
    def __init__(self, mixed=False, prekeyed=False, trace16=False):
        self.ops = []
        self.product_order = NORM_ORDER
        self.directions = MUL_DIRECTIONS[0]
        self.mixed = mixed
        self.prekeyed = prekeyed
        self.trace16 = trace16
        self.inputs = {f"a{i}": f"{64*i}(%rdi)" for i in range(64)}
        if mixed:
            self.inputs.update({f"a{i}": f"{64*(i-32)}(%r10)" for i in range(32,48)})
        self.inputs.update({f"m{i}": f"noid_internal_matrix+{8*i}(%rip)" for i in range(16+len(EXTRA_COEFFS))})

    def seventh(self):
        value = [f"a{i}" for i in range(16)]
        if not self.mixed and not self.prekeyed:
            value = [self.emit("constant", operand, i) for i, operand in enumerate(value)]
        # In the top quadratic extension, x = a + t*w, with trace t
        # and norm n = a*(a+t) + d*t^2, where d is the norm of w.
        # From x^2 = t*x + n, x^7 = (t^6 + r)*x + r*t,
        # with r = n*(t^2 + n)^2.
        half = len(value) // 2
        a, t = value[:half], value[half:]
        self.directions = MUL_DIRECTIONS[0]
        norm_product = self.multiply(a, self.add(a, t))
        t2 = self.square(t)
        scaled_trace = self.times_top_norm_root(t2)
        norm = self.add(norm_product, scaled_trace)
        self.product_order = CORRECTION_ORDER
        trace_plus_norm = self.add(t2, norm)
        self.directions = MUL_DIRECTIONS[1]
        correction = self.multiply(norm, self.square(trace_plus_norm))
        self.product_order = TRACE_ORDER
        self.directions = MUL_DIRECTIONS[2]
        t6 = self.square(self.multiply(t, t2))
        coefficient = self.add(t6, correction)
        self.product_order = OUTPUT_ORDER
        if self.trace16:
            for i, value in enumerate(self.trace_product16(coefficient, t)):
                self.emit("store", value, 64*i)
            return
        out_low, out_high = self.multiply_outputs((coefficient, a, correction, t, coefficient, t))
        output = out_low + out_high
        if self.mixed:
            self.internal(output)
        else:
            for i, (lo, hi) in enumerate(zip(out_low, out_high)):
                self.emit("store", lo, 64*i)
                self.emit("store", hi, 64*(i+half))

    def trace_product16(self, a, b):
        """Trace GF(2^64) -> GF(2^16) of a*b, in the nested trace-one basis.

        The trace is the last quadratic coefficient at each higher level.
        Its Gram matrix on (1,u,v,uv) is
            [0 0 0 1; 0 0 1 1; 0 1 0 1; 1 1 1 1].
        Four GF(2^16) products suffice. Their shared normal-basis norm
        reduction is applied once after the three byte-product sums.
        """
        aa = [a[i:i+2] for i in range(0,8,2)]
        b0,b1,b2,b3 = [b[i:i+2] for i in range(0,8,2)]
        bb = [b3, self.add(b2,b3), self.add(b1,b3),
              self.add(self.add(b0,b1), self.add(b2,b3))]
        def dot(left, right):
            terms = [self.emit("mul", x, y) for x,y in zip(left,right)]
            return self.emit("xor3", self.emit("xor", terms[0],terms[1]), terms[2],terms[3])
        low = dot([x[0] for x in aa],[x[0] for x in bb])
        high = dot([x[1] for x in aa],[x[1] for x in bb])
        sums = dot([self.emit("xor", *x) for x in aa],
                   [self.emit("xor", *x) for x in bb])
        scaled = self.emit("linear", sums, "m7")
        return [self.emit("xor", low, scaled), self.emit("xor", high, scaled)]

    def internal(self, output):
        # The untouched words use u,v,z = T*(s1,s2,s3).  The exact transformed
        # system is u'=f+0x009d*u+0x42ab*v, v'=z, z'=0x0029*u+0x43aa*v.
        # Store z' over old v, then rotate v/z pointers; no copy of z is needed.
        def lin(value, index):
            return self.emit("linear", value, f"m{index}")
        for i in range(0, 16, 2):
            f = output[i:i+2]
            u = [f"a{16+i}", f"a{17+i}"]
            v = [f"a{32+i}", f"a{33+i}"]
            for x in u+v:
                self.emit("preload", x)
            # s0'=(d0+1)*f+u plus the next partial round's key.
            carry = [self.emit("keyed_xor", lin(x,7), y, i+j)
                     for j,(x,y) in enumerate(zip(f,u))]
            us = self.emit("xor", *u)
            c2s = lin(us,9)
            c2 = [self.emit("xor", lin(u[0],8),c2s), c2s]
            gs = lin(us,11)
            gu = [self.emit("xor",lin(u[0],10),gs),gs]
            cv = [self.emit("xor",lin(v[0],15),lin(v[1],14)),
                  self.emit("xor",lin(v[0],14),v[1])]
            znew = self.add(gu,cv)
            av = self.add(cv,v)
            unew = [self.emit("xor3", x,y,z) for x,y,z in zip(f,c2,av)]
            for j in range(2):
                self.emit("carry",carry[j],i+j)
                self.emit("store",unew[j],64*(16+i+j))
                self.emit("store",znew[j],64*(32+i+j))

    def add(self, left, right):
        return [self.emit("xor", x, y) for x, y in zip(left, right)]

    def times_inverse_root16(self, values):
        low, high = values
        return [high, self.emit("xor", low, self.emit("affine", high))]

    def times_norm32(self, values):
        low, high = values
        return [self.emit("xor", high, self.emit("affine", low)), low]

    def times_norm64(self, values):
        low, high = values[:2], values[2:]
        return high + self.times_inverse_root16(self.add(low, high))

    def times_top_norm_root(self, values):
        low, high = values[:4], values[4:]
        product_lo = self.times_inverse_root16(high[:2]) + self.times_inverse_root16(high[2:])
        combined = self.add(low, high)
        u = self.times_inverse_root16(combined[:2])
        return product_lo + self.add(u, combined[2:]) + u

    def square(self, values):
        if len(values) == 1:
            return [self.emit("square", values[0])]
        half = len(values) // 2
        low, high = values[:half], values[half:]
        hi = self.square(high)
        lo = self.square(low)
        if len(values) == 2:
            pair_sum = self.emit("xor", low[0], high[0])
            scaled = [self.emit("sqalpha", pair_sum)]
            return self.add(lo, scaled) + self.add(hi, scaled)
        scaled_hi = self.times_norm32(hi) if len(values) == 4 else self.times_norm64(hi)
        return self.add(lo, scaled_hi) + hi

    def emit(self, op, *args):
        dest = len(self.ops)
        self.ops.append((op, dest, args))
        return dest

    def multiply_outputs(self, operands):
        if len(operands[0]) == 1:
            c, a, r, t, c2, t2 = (value[0] for value in operands)
            direct = self.emit("mul", c, a)
            adjust = self.emit("mul", r, t)
            return [self.emit("xor", direct, adjust)], [self.emit("mul", c2, t2)]

        half = len(operands[0]) // 2
        level = half.bit_length() - 1
        order = self.product_order[level]
        directions = OUTPUT_DIRECTIONS[level]
        products = {}
        for part in order:
            if part == "l":
                args = [value[:half] for value in operands]
            elif part == "h":
                args = [value[half:] for value in operands]
            else:
                args = [self.add(value[:half], value[half:]) for value in operands]
            scaled_branch = part == ("s" if half == 1 else "h")
            if scaled_branch and directions >= 0:
                for index in range(3):
                    slot = index * 2 + bool(directions & (1 << index))
                    args[slot] = self.scale_product(args[slot])
            products[part] = self.multiply_outputs(args)
        return tuple(self.reduce_product(*column, prescaled=directions >= 0)
                     for column in zip(*(products[key] for key in "lhs")))

    def scale_product(self, value):
        if len(value) == 4:
            return self.times_norm64(value)
        if len(value) == 2:
            return self.times_norm32(value)
        return [self.emit("linear", value[0], "m7")]

    def reduce_product(self, lo, hi, total, prescaled=False):
        if len(hi) == 1:
            scaled = total if prescaled else self.scale_product(total)
            return self.add(lo, scaled) + self.add(hi, scaled)
        scaled = hi if prescaled else self.scale_product(hi)
        return self.add(lo, scaled) + self.add(lo, total)

    def multiply(self, a, b):
        if len(a) == 1:
            return [self.emit("mul", a[0], b[0])]
        half = len(a) // 2
        level = half.bit_length() - 1
        order = self.product_order[level]
        direction = self.directions[level]
        products = {}
        for part in order:
            if part == "l":
                left, right = a[:half], b[:half]
            elif part == "h":
                left, right = a[half:], b[half:]
            else:
                left = self.add(a[:half], a[half:])
                right = self.add(b[:half], b[half:])
            scaled_branch = part == ("s" if half == 1 else "h")
            if scaled_branch:
                if direction == 1:
                    left = self.scale_product(left)
                elif direction == 2:
                    right = self.scale_product(right)
            products[part] = self.multiply(left, right)

        return self.reduce_product(*(products[key] for key in "lhs"), prescaled=bool(direction))



def vector_operands(op, args):
    if op in ("constant", "store", "carry", "preload"):
        return args[:1]
    if op in ("keyed_xor", "linear_key"):
        return args[:2]
    return args


def reuse_expressions(ops):
    """Share repeated Karatsuba inputs and remove unused producers."""
    values = {}
    aliases = {}
    unique = []
    for op, dest, args in ops:
        operands = vector_operands(op, args)
        args = tuple(aliases.get(value, value) for value in operands) + args[len(operands):]
        key = op, tuple(sorted(args, key=str)) if op in ("xor", "xor3", "mul") else args
        if op not in ("store", "carry", "preload") and key in values:
            aliases[dest] = values[key]
        else:
            values[key] = dest
            unique.append((op, dest, args))

    needed = set()
    result = []
    for op, dest, args in reversed(unique):
        if op in ("store", "carry", "preload") or dest in needed:
            needed.update(vector_operands(op, args))
            result.append((op, dest, args))
    return list(reversed(result))


def fold_xors(ops):
    """Merge single-use XOR producers where one ternary logic instruction fits."""
    definitions = {dest: (op, args) for op, dest, args in ops}
    consumers = Counter(
        value for op, _, args in ops
        for value in (vector_operands(op, args))
    )
    aliases = {}
    drop = set()
    new = []
    for op, dest, args in ops:
        operands = vector_operands(op, args)
        args = tuple(aliases.get(arg, arg) for arg in operands) + args[len(operands):]
        if op in ("xor", "xor3"):
            terms = list(args)
            changed = True
            while changed:
                changed = False
                for value in terms:
                    producer, inner = definitions.get(value, (None, ()))
                    if producer not in ("xor", "xor3") or consumers[value] != 1:
                        continue
                    updated = [term for term in terms if term != value]
                    for term in inner:
                        if term in updated:
                            updated.remove(term)
                        else:
                            updated.append(term)
                    if len(updated) <= 3:
                        drop.add(value)
                        terms = updated
                        changed = True
                        break
            if len(terms) == 1:
                aliases[dest] = terms[0]
                continue
            if len(terms) < 1:
                raise ValueError("Unexpected zero")
            op = "xor" if len(terms) == 2 else "xor3"
            args = tuple(terms)
            definitions[dest] = op, args
        new.append((op, dest, args))
    return [row for row in new if row[1] not in drop]


def affine_coefficient(name):
    matrix = int(re.search(rf"{name} UINT64_C\((0x[0-9a-f]+)\)",
                           Path(__file__).with_name("noid_aes_tables.h").read_text())[1], 16)
    return sum(((matrix >> (8 * (7 - i))) & 1) << i for i in range(8))


AFFINE_MULTIPLIERS = {
    "affine": affine_coefficient("NOID_AES_MUL_ALPHA"),
    "square": 1,
    **{f"m{16+i}": v for i,v in enumerate(EXTRA_COEFFS)},
    "m8": 0x9d,
    "m9": 0x2e,
    "m10": 0x29,
    "m11": 0xe0,
    "m12": 0xaa,
    "m13": 0x43,
    "m14": 0x42,
    "m15": 0xe8,
    "sqalpha": affine_coefficient("NOID_AES_NORM16"),
    **{f"m{i}": affine_coefficient(name) for i, name in enumerate((
        "NOID_AES_DIAG_0",
        "NOID_AES_DIAG_1_HIGH", "NOID_AES_DIAG_1_SCALED",
        "NOID_AES_DIAG_2_HIGH", "NOID_AES_DIAG_2_SCALED",
        "NOID_AES_DIAG_3_HIGH", "NOID_AES_DIAG_3_SCALED",
        "NOID_AES_NORM16",
    ))},
}


def form_add(a, b):
    out = {(atom, power): scale for atom, power, scale in a}
    for atom, power, scale in b:
        out[atom, power] = out.get((atom, power), 0) ^ scale
    return frozenset((atom, power, scale) for (atom, power), scale in out.items() if scale)


def remove_affine_work(ops):
    """Replace a byte-field map with an available equivalent XOR."""
    form = {}
    known = {}
    recent = deque(maxlen=XOR_SEARCH)
    aliases = {}
    result = []
    affine = ("affine", "linear", "square", "sqalpha")

    def get(value):
        return form.get(value, frozenset(((value, 0, 1),)))

    for op, dest, args in ops:
        operands = vector_operands(op,args)
        args = tuple(aliases.get(value,value) for value in operands) + args[len(operands):]
        if op in ("xor", "xor3"):
            identity = frozenset()
            for arg in args:
                identity = form_add(identity, get(arg))
        elif op in affine:
            power = int(op in ("square", "sqalpha"))
            c = AFFINE_MULTIPLIERS[args[1] if op == "linear" else op]
            identity = frozenset((atom, (p+power)%8, aes_product(c, aes_product(k,k) if power else k))
                                 for atom, p, k in get(args[0]))
            for value in reversed(recent):
                other = known.get(form_add(identity, get(value)))
                if other is not None and other != value:
                    op, args = "xor", (value, other)
                    break
            else:
                candidates = list(recent)[-TERNARY_SEARCH:] if TERNARY_SEARCH else ()
                for i, first in enumerate(reversed(candidates)):
                    missing = form_add(identity, get(first))
                    for second in candidates[:len(candidates)-i-1]:
                        if first == second:
                            continue
                        other = known.get(form_add(missing, get(second)))
                        if other is not None and other not in (first, second):
                            op, args = "xor3", (first, second, other)
                            break
                    if op == "xor3":
                        break
        else:
            identity = get(dest)
        if op in ("xor", "xor3", *affine) and identity in known:
            aliases[dest] = known[identity]
            continue
        for arg in vector_operands(op, args):
            if isinstance(arg, str) and arg.startswith("a"):
                initial = get(arg)
                if initial not in known:
                    known[initial] = arg
                    recent.append(arg)
        form[dest] = identity
        known[identity] = dest
        recent.append(dest)
        result.append((op, dest, args))
    return result


SCHEDULED_PARTIAL_IDS = [5, 1, 0, 8, 2, 43, 3, 47, 9, 18, 12, 6, 16, 17, 10, 28, 29, 45, 13, 11, 19, 34, 14, 15, 21, 25, 4, 20, 31, 7, 46, 24, 22, 23, 32, 33, 40, 35, 51, 38, 49, 39, 44, 42, 50, 41, 59, 54, 55, 52, 53, 48, 57, 58, 60, 56, 66, 62, 70, 61, 71, 64, 74, 77, 112, 73, 89, 85, 63, 75, 67, 76, 88, 99, 65, 93, 92, 86, 81, 98, 87, 80, 90, 82, 96, 132, 91, 83, 97, 100, 131, 94, 111, 104, 133, 95, 134, 130, 102, 136, 142, 109, 135, 101, 147, 105, 138, 114, 115, 113, 148, 139, 118, 103, 121, 120, 143, 137, 119, 144, 149, 146, 150, 145, 151, 152, 154, 153, 155, 170, 162, 171, 163, 161, 158, 159, 160, 167, 166, 175, 168, 176, 182, 164, 183, 178, 191, 174, 179, 190, 184, 169, 173, 177, 180, 181, 188, 189, 197, 186, 194, 221, 187, 196, 192, 246, 238, 199, 215, 200, 240, 220, 195, 185, 204, 222, 193, 201, 216, 217, 223, 247, 205, 202, 214, 198, 219, 218, 226, 206, 227, 203, 210, 211, 207, 213, 212, 229, 235, 231, 236, 250, 228, 230, 234, 248, 239, 241, 232, 254, 252, 273, 262, 243, 277, 237, 261, 249, 255, 245, 251, 242, 265, 276, 263, 274, 253, 272, 258, 244, 259, 315, 275, 278, 271, 279, 270, 264, 268, 269, 300, 280, 284, 288, 281, 289, 301, 282, 285, 283, 290, 291, 292, 294, 306, 287, 296, 293, 286, 303, 304, 318, 305, 320, 310, 316, 307, 341, 295, 312, 317, 297, 323, 327, 356, 331, 342, 343, 326, 335, 319, 311, 321, 322, 363, 334, 414, 345, 330, 313, 339, 344, 332, 362, 325, 402, 333, 368, 324, 351, 348, 358, 360, 380, 389, 349, 361, 378, 403, 350, 379, 381, 359, 387, 352, 355, 369, 375, 388, 390, 357, 383, 371, 373, 386, 372, 370, 382, 354, 437, 374, 392, 385, 353, 393, 421, 399, 394, 384, 395, 398, 405, 407, 401, 406, 423, 422, 412, 417, 409, 400, 415, 408, 420, 416, 427, 426, 418, 404, 445, 438, 419, 432, 428, 424, 429, 439, 433, 441, 455, 444, 452, 435, 459, 450, 446, 453, 465, 434, 458, 467, 464, 480, 478, 479, 454, 457, 456, 447, 466, 481, 472, 476, 462, 470, 442, 474, 488, 505, 473, 477, 484, 471, 483, 482, 500, 475, 501, 485, 490, 487, 546, 486, 494, 491, 506, 499, 504, 502, 507, 513, 489, 497, 495, 510, 498, 572, 496, 547, 515, 523, 503, 511, 527, 529, 512, 516, 524, 555, 517, 560, 568, 526, 528, 522, 543, 525, 542, 558, 554, 574, 571, 570, 567, 569, 583, 557, 582, 576, 592, 595, 575, 596, 580, 599, 579, 585, 597, 593, 594, 601, 600, 613, 646, 603, 642, 630, 628, 616, 627, 643, 645, 614, 624, 644, 602, 667, 651, 652, 626, 623, 625, 653, 696, 631, 639, 654, 641, 668, 640, 672, 682, 671, 611, 680, 683, 610, 685, 632, 708, 693, 739, 699, 695, 697, 692, 717, 694, 679, 701, 705, 700, 707, 720, 710, 704, 718, 721, 719, 722, 726, 724, 736, 738, 725, 752, 728, 741, 735, 748, 753, 755, 751, 749, 768, 765, 727, 772, 764, 766, 750, 773, 769, 756, 757, 806, 767, 781, 783, 784, 796, 786, 794, 780, 793, 811, 795, 798, 797, 801, 800, 805, 809, 808, 802, 819, 822, 821, 818, 820, 825, 823, 836, 829, 826, 842, 827, 839, 840, 837, 864, 850, 849, 851, 858, 862, 870, 853, 828, 863, 867, 874, 856, 854, 857, 900, 861, 868, 869, 852, 871, 891, 873, 893, 876, 872, 892, 901, 875, 902, 884, 889, 897, 890, 866, 895, 898, 903, 865, 905, 894, 910, 896, 927, 911, 906, 880, 921, 907, 913, 915, 882, 933, 930, 923, 909, 925, 922, 924, 955, 916, 932, 934, 926, 929, 939, 937, 935, 938, 928, 953, 912, 881, 941, 942, 967, 883, 945, 956, 964, 985, 947, 965, 946, 966, 944, 957, 961, 954, 975, 971, 959, 970, 958, 962, 969, 960, 999, 948, 996, 879, 914, 973, 987, 974, 976, 988, 980, 878, 989, 977, 997, 994, 998, 986, 993, 877, 990, 991, 1002, 992, 1019, 978, 1001, 1003, 1006, 1020, 1009, 1005, 1029, 1031, 1028, 1030, 1008, 1017, 1023, 979, 1021, 1012, 1022, 1011, 1010, 1061, 1024, 1025, 1038, 1051, 1035, 1018, 1034, 1026, 1052, 1033, 1043, 1037, 1060, 1055, 1041, 1053, 1062, 1057, 1063, 1067, 1058, 1040, 1039, 1044, 1049, 1054, 1066, 1056, 1050, 1065, 1087, 1090, 1069, 1070, 1074, 1092, 1073, 1093, 1081, 1042, 1071, 1082, 1083, 1072, 1085, 1076, 1084, 1115, 1094, 1095, 1089, 1106, 1086, 1088, 1099, 1101, 1104, 1117, 1116, 1102, 1107, 1098, 1127, 1125, 1124, 1113, 1097, 1126, 1114, 1122, 1138, 1121, 1119, 1118, 1129, 1134, 1007, 1103, 1075, 1131, 943, 1139, 1120, 1108, 1135, 1133, 1130, 1105, 1136, 1137, 1140]

class Assembly:
    def __init__(self, product):
        self.product = product
        self.lines = []
        self.use = defaultdict(deque)
        self.reg = {}
        self.memory = dict(product.inputs)
        self.stack = {}
        self.free_slots = []
        self.slots = 0
        self.carry = {}
        if product.mixed:
            # These state vectors have no valid memory copy until spilled.
            # The other three state words continue to live in the state array.
            for index in range(16):
                value = f"a{index}"
                self.reg[value] = index
                del self.memory[value]

    def line(self, asm):
        self.lines.append("\t" + asm)

    def save(self, value):
        if value not in self.memory:
            if self.free_slots:
                offset = self.free_slots.pop()
            else:
                offset = self.slots * 64
                self.slots += 1
            address = f"{offset}(%rsp)"
            self.line(f"vmovdqu64 %zmm{self.reg[value]}, {address}")
            self.memory[value] = address
            self.stack[value] = offset

    def kill(self, value):
        self.reg.pop(value, None)
        if value in self.stack:
            self.free_slots.append(self.stack.pop(value))

    def allocate(self, keep):
        occupied = set(self.reg.values())
        registers = list(range(29)) + ([30] if SQUARE_AS_MUL else [])
        if MEMORY_MATRIX_MASK & 256:
            registers.append(31)
        if MEMORY_MATRIX_MASK & 512:
            registers.append(29)
        for i in registers:
            if i not in occupied:
                return i
        options = [value for value in self.reg if value not in keep]
        # Prefer the most distant next use, breaking ties in favor of
        # a value that already has a copy in memory.
        victim = max(options, key=lambda v: (self.use[v][0] + MEMORY_BIAS * (v in self.memory),
                                             v in self.memory))
        self.save(victim)
        return self.reg.pop(victim)

    def register(self, value, keep):
        if value not in self.reg:
            reg = self.allocate(keep)
            mnemonic = "vpbroadcastq" if isinstance(value, str) and value.startswith("m") else "vmovdqu64"
            self.line(f"{mnemonic} {self.memory[value]}, %zmm{reg}")
            self.reg[value] = reg
        return self.reg[value]

    def restore_carry(self):
        """Move the next S-box input to zmm0–15 without losing a live source."""
        pending = {
            dst: ("%zmm" + str(self.reg[value]) if value in self.reg else self.memory[value])
            for dst, value in self.carry.items()
            if self.reg.get(value) != dst
        }
        pending = {f"%zmm{dst}": src for dst, src in pending.items()}
        while pending:
            sources = set(pending.values())
            destinations = [dst for dst in pending if dst not in sources]
            if destinations:
                for dst in destinations:
                    self.line(f"vmovdqu64 {pending.pop(dst)}, {dst}")
            else:
                # Once acyclic copies are resolved, all remaining source
                # registers are among the first sixteen destinations.
                scratch = "%zmm16"
                dst = next(iter(pending))
                self.line(f"vmovdqa64 {dst}, {scratch}")
                pending = {key: scratch if value == dst else value
                           for key, value in pending.items()}

    def generate(self):
        self.product.ops = reuse_expressions(fold_xors(reuse_expressions(self.product.ops)))
        self.product.ops = reuse_expressions(fold_xors(reuse_expressions(remove_affine_work(self.product.ops))))
        if self.product.mixed:
            # This dependency-safe schedule was selected from the actual
            # register allocator's output; arithmetic and round counts match.
            order = SCHEDULED_PARTIAL_IDS
            by_id = {d:(o,d,a) for o,d,a in self.product.ops if o != "preload"}
            if set(order) != set(by_id) or len(order) != len(by_id):
                raise ValueError("Partial arithmetic changed; regenerate its schedule")
            position = {d: i for i, d in enumerate(order)}
            for op, dest, args in by_id.values():
                for operand in vector_operands(op, args):
                    if isinstance(operand, int) and (
                            operand not in position or position[operand] >= position[dest]):
                        raise ValueError("Invalid scheduled data dependency")
                if op == "store":
                    if args[1] % 64:
                        raise ValueError("Unexpected partial-state store alignment")
                    overwritten = f"a{args[1] // 64}"
                    for other_op, reader, other_args in by_id.values():
                        if overwritten in vector_operands(other_op, other_args):
                            if position[reader] >= position[dest]:
                                raise ValueError("State store precedes original input use")
            self.product.ops = [by_id[d] for d in order]
        constants = {dest: (op, dest, args)
                     for op, dest, args in self.product.ops if op == "constant"}
        scheduled = []
        for op, dest, args in self.product.ops:
            if op == "constant":
                continue
            for operand in vector_operands(op, args):
                if operand in constants:
                    scheduled.append(constants.pop(operand))
            scheduled.append((op, dest, args))
        self.product.ops = scheduled
        for step, (op, _, args) in enumerate(self.product.ops):
            for value in vector_operands(op, args):
                self.use[value].append(step)
        for op, _, args in self.product.ops:
            if op == "carry":
                # Keep carried values live until the parallel move at the
                # end of the round. The allocator can still spill them.
                self.use[args[0]].append(len(self.product.ops))
        for step, (op, dest, args) in enumerate(self.product.ops):
            operands = vector_operands(op, args)
            for value in operands:
                self.use[value].popleft()
            keep = set(operands)
            if op == "carry":
                self.carry[args[1]] = args[0]
            elif op == "preload":
                self.register(args[0], keep)
            elif op == "store":
                reg = self.register(args[0], keep)
                if self.product.trace16:
                    self.line(f"vmovdqu64 %zmm{reg}, {args[1]}(%rsi)")
                elif self.product.mixed and 2048 <= args[1] < 3072:
                    self.line(f"vmovdqu64 %zmm{reg}, {args[1]-2048}(%r10)")
                else:
                    self.line(f"vmovdqu64 %zmm{reg}, {args[1]}(%rdi)")
            elif op == "constant":
                target = self.allocate(keep)
                self.line(f"vpbroadcastb {args[1]}(%rsi), %zmm{target}")
                self.line(f"vpxord {self.memory[args[0]]}, %zmm{target}, %zmm{target}")
                self.reg[dest] = target
            elif op == "keyed_xor":
                left, right = sorted(args[:2], key=lambda v: bool(self.use[v]))
                target = self.register(left, keep)
                other = self.register(right, keep)
                if self.use[left]:
                    new_target = self.allocate(keep)
                    self.line(f"vmovdqa64 %zmm{target}, %zmm{new_target}")
                    target = new_target
                self.line(f"vpternlogd $150, {4*args[2]}(%rsi){{1to16}}, %zmm{other}, %zmm{target}")
                self.reg[dest] = target
            elif op in ("linear", "linear_key"):
                reg = self.register(args[0], keep)
                matrix_index = int(args[1][1:])
                if MEMORY_MATRIX_MASK & (1 << matrix_index):
                    matrix_operand = f"noid_internal_matrix+{8*matrix_index}(%rip){{1to8}}"
                else:
                    matrix_operand = f"%zmm{self.register(args[1], keep)}"
                target = self.allocate(keep) if self.use[args[0]] else reg
                immediate = args[2] if op == "linear_key" else 0
                self.line(f"vgf2p8affineqb ${immediate}, {matrix_operand}, %zmm{reg}, %zmm{target}")
                self.reg[dest] = target
            elif op in ("affine", "square", "sqalpha"):
                # GFNI's matrix is its memory-capable operand. The data is a register.
                reg = self.register(args[0], keep)
                target = self.allocate(keep) if self.use[args[0]] else reg
                if op == "square" and SQUARE_AS_MUL:
                    self.line(f"vgf2p8mulb %zmm{reg}, %zmm{reg}, %zmm{target}")
                else:
                    matrix = {"affine": 31, "square": 30, "sqalpha": 29}[op]
                    flag = {"affine": 8, "sqalpha": 9, "square": 10}[op]
                    index = {"affine": 0, "sqalpha": 1, "square": 2}[op]
                    matrix_operand = (f"noid_sbox_matrix+{8*index}(%rip){{1to8}}"
                                      if MEMORY_MATRIX_MASK & (1 << flag)
                                      else f"%zmm{matrix}")
                    self.line(f"vgf2p8affineqb $0, {matrix_operand}, %zmm{reg}, %zmm{target}")
                self.reg[dest] = target
            elif op in ("mul", "xor"):
                a, b = args
                if a not in self.reg and b in self.reg:
                    a, b = b, a
                left = self.register(a, keep)
                if b in self.reg or (self.use[b] and self.use[b][0] <= step + RELOAD_WINDOW):
                    right = f"%zmm{self.register(b, keep)}"
                else:
                    right = self.memory[b]
                target = next((self.reg[v] for v in (a, b) if v in self.reg and not self.use[v]), None)
                if target is None:
                    target = self.allocate(keep)
                mnemonic = "vgf2p8mulb" if op == "mul" else "vpxord"
                self.line(f"{mnemonic} {right}, %zmm{left}, %zmm{target}")
                self.reg[dest] = target
            elif op == "xor3":
                ordered = sorted(args, key=lambda v: (v not in self.reg, bool(self.use[v])))
                a, b, c = ordered
                first = self.register(a, keep)
                second = self.register(b, keep)
                if c in self.reg or (self.use[c] and self.use[c][0] <= step + RELOAD_WINDOW):
                    last = f"%zmm{self.register(c, keep)}"
                else:
                    last = self.memory[c]
                if self.use[a]:
                    target = self.allocate(keep)
                    self.line(f"vmovdqa64 %zmm{first}, %zmm{target}")
                else:
                    target = first
                self.line(f"vpternlogd $150, {last}, %zmm{second}, %zmm{target}")
                self.reg[dest] = target
            else:
                raise ValueError(f"unknown vector operation {op}")
            for value in operands:
                if not self.use[value]:
                    self.kill(value)
        if self.product.trace16:
            name = "noid_seventh_trace16_asm"
        elif self.product.mixed:
            name = "noid_partial_rounds_asm"
        elif self.product.prekeyed:
            name = "noid_seventh_prekeyed_asm"
        else:
            name = "noid_seventh_asm"
        size = self.slots * 64
        prologue = [
            "\t.text", "\t.p2align 5", f"\t.globl {name}",
            f"\t.hidden {name}", f"\t.type {name}, @function", f"{name}:",
            "\t.cfi_startproc", "\tvpbroadcastq %rdx, %zmm31",
            "\tvpbroadcastq %r8, %zmm29",
        ]
        if MEMORY_MATRIX_MASK & 256:
            prologue.remove("\tvpbroadcastq %rdx, %zmm31")
        if MEMORY_MATRIX_MASK & 512:
            prologue.remove("\tvpbroadcastq %r8, %zmm29")
        if not SQUARE_AS_MUL and not MEMORY_MATRIX_MASK & 1024:
            prologue.append("\tvpbroadcastq %rcx, %zmm30")
        epilogue = [
            "\tret", "\t.cfi_endproc", f"\t.size {name}, .-{name}",
            '\t.section .note.GNU-stack,"",@progbits',
        ]
        if size:
            # r11 keeps the caller's stack pointer for both return and
            # unwinding across the dynamically aligned scratch space.
            prologue += ["\tmovq %rsp, %r11", "\t.cfi_def_cfa %r11, 8",
                         "\tandq $-64, %rsp", f"\tsubq ${size}, %rsp"]
            epilogue[:0] = ["\tmovq %r11, %rsp", "\t.cfi_def_cfa %rsp, 8"]
        if self.product.mixed:
            for index in range(16):
                prologue.append(f"\tvmovdqu64 {64*index}(%rdi), %zmm{index}")
            prologue += ["\tleaq 2048(%rdi), %r10", "\tleaq 3072(%rdi), %rax", "\tmovl $58, %r9d", "\t.p2align 5", ".Lpartial_round:"]
            self.restore_carry()
            self.line("xchgq %r10, %rax")
            self.line("addq $64, %rsi")
            self.line("decl %r9d")
            self.line("jnz .Lpartial_round")
            for index in range(16):
                self.line(f"vmovdqu64 %zmm{index}, {64*index}(%rdi)")
        return "\n".join(prologue + self.lines + epilogue) + "\n"


def external_keyed_assembly():
    directory = Path(__file__).resolve().parent
    aes = (directory / "noid_aes_tables.h").read_text()
    rounds = load_array((directory / "noid_tables.h").read_text(), "noid_round")
    f2new = load_array(aes, "f2new")
    two, four = [re.search(r"^#define NOID_AES_MUL_" + name + r" UINT64_C\((0x[0-9a-f]+)\)",
                          aes, re.M)[1] for name in ("TWO", "FOUR")]

    # Fold each next-round key into the immediate XOR of the four affine
    # instructions. The keyed r1/r3 also feed r0/r2.
    for r in (0, 1, 2, 3, 4, 63, 64, 65):
        name = f"noid_external_keyed_{r}"
        keys = [apply_basis(f2new, rounds[66*j+r]).to_bytes(16, "little")
                if r != 4 or j == 0 else bytes(16) for j in range(4)]
        lines = [
            ".text", ".p2align 5", f".globl {name}", f".hidden {name}",
            f".type {name}, @function", f"{name}:", ".cfi_startproc",
            f"movabsq ${two}, %rax", "vpbroadcastq %rax, %zmm8",
            f"movabsq ${four}, %rax", "vpbroadcastq %rax, %zmm9",
        ]
        for i, (k0, k1, k2, k3) in enumerate(zip(*keys)):
            lines += [f"vmovdqa64 {64*(j*16+i)}(%rdi), %zmm{j}" for j in range(4)]
            lines += [
                "vpxord %zmm0, %zmm1, %zmm4",
                "vpxord %zmm2, %zmm3, %zmm5",
                f"vgf2p8affineqb ${k2 ^ k3}, %zmm8, %zmm1, %zmm1",
                f"vgf2p8affineqb ${k0 ^ k1}, %zmm8, %zmm3, %zmm3",
                f"vgf2p8affineqb ${k1 ^ k2 ^ k3}, %zmm9, %zmm4, %zmm6",
                f"vgf2p8affineqb ${k3 ^ k0 ^ k1}, %zmm9, %zmm5, %zmm7",
                "vpternlogd $150, %zmm1, %zmm5, %zmm6",
                "vpternlogd $150, %zmm3, %zmm4, %zmm7",
                "vpternlogd $150, %zmm4, %zmm6, %zmm3",
                "vpternlogd $150, %zmm5, %zmm7, %zmm1",
            ]
            lines += [f"vmovdqa64 %zmm{reg}, {64*(j*16+i)}(%rdi)"
                      for j, reg in enumerate((3, 6, 1, 7))]
        lines += ["ret", ".cfi_endproc", f".size {name}, .-{name}"]
        print("\n".join(lines))


def external_sparse4_assembly():
    # Algebraically combine external4 with the sparse-basis entry.
    # If U=a+b,V=c+d,W=b+d, u=4U, D1=5(U+V)+2W, D2=2b+V.
    # The other T rows sum to zero, so only D1 and D2 are needed.
    program = TowerProgram(False, True)
    coeffs = [155, 197, 155, 239, 76, 4, 75, 154, 157, 46, 41, 224, 170, 67, 66, 232, 77, 250, 133, 241, 236, 134, 105, 119, 24, 34, 206, 191, 214, 251]
    transform = [[257, 257, 257], [34048, 60416, 26880], [6301, 52770, 54975]]
    directory = Path(__file__).resolve().parent
    aes = (directory / "noid_aes_tables.h").read_text()
    rounds = load_array((directory / "noid_tables.h").read_text(), "noid_round")
    f2new = load_array(aes, "f2new")
    keys = apply_basis(f2new,rounds[4]).to_bytes(16,"little")
    def lin(v,c,key=0):
        if c==1 and not key:return v
        return program.emit("linear_key",v,f"m{coeffs.index(c)}",key) if key else program.emit("linear",v,f"m{coeffs.index(c)}")
    def add(a,b):return program.emit("xor",a,b)
    def mul16(pair,c):
        lo,hi=c&255,c>>8
        if lo==hi:return [lin(x,lo) for x in pair]
        shared=lin(add(*pair),aes_product(lo^hi,0x9a))
        return [add(lin(x,k),shared) if k else shared for x,k in zip(pair,(lo,hi))]
    for i in range(0,16,2):
        original=[[f"a{16*j+i}",f"a{16*j+i+1}"]for j in range(4)]
        state0=[];state1=[];d1=[];d2=[]
        for b in range(2):
            a,bv,c,d=[row[b]for row in original]
            u=add(a,bv);v=add(c,d);w=add(bv,d)
            first=add(lin(add(u,v),251),lin(w,77))
            second=add(lin(bv,77),v)
            state0.append(add(first,lin(v,250,keys[i+b])))
            state1.append(lin(u,250))
            d1.append(first);d2.append(second)
        result=[state0,state1]
        for row in transform[1:]:
            products=[mul16(pair,k)for pair,k in zip((d1,d2),row[:2])]
            result.append([add(*col)for col in zip(*products)])
        for j,row in enumerate(result):
            for b,x in enumerate(row):program.emit("store",x,64*(16*j+i+b))
    code=Assembly(program).generate().replace("noid_seventh_prekeyed_asm","noid_external_sparse4_asm")
    code=code.replace("\tvpbroadcastq %rdx, %zmm31\n","").replace("\tvpbroadcastq %r8, %zmm29\n","")
    print(code,end="")

FULL_ROUND_TUNING = {'XOR_SEARCH': 60, 'TERNARY_SEARCH': 36, 'MUL_DIRECTIONS': ((1, 1, 1), (1, 1, 1), (1, 1, 1)), 'OUTPUT_DIRECTIONS': (7, 7, 7), 'MEMORY_BIAS': 24, 'MEMORY_MATRIX_MASK': 896, 'NORM_ORDER': ('slh', 'lsh', 'lsh'), 'CORRECTION_ORDER': ('slh', 'lsh', 'lsh'), 'TRACE_ORDER': ('hsl', 'hls', 'slh'), 'OUTPUT_ORDER': ('hsl', 'slh', 'hsl'), 'RELOAD_WINDOW': 3}
PARTIAL_ROUND_TUNING = dict(FULL_ROUND_TUNING)

FUSED_DYNAMIC_ROUNDS=(1,2,3,64,65)

def fused_dynamic_inverse(matrix):
    n=len(matrix)
    a=[row[:]+[int(i==j) for j in range(n)] for i,row in enumerate(matrix)]
    for i in range(n):
        pivot=next(j for j in range(i,n) if a[j][i])
        a[i],a[pivot]=a[pivot],a[i]
        inv=next(k for k in range(1,256) if aes_product(k,a[i][i])==1)
        a[i]=[aes_product(x,inv) for x in a[i]]
        for j in range(n):
            if j==i:continue
            c=a[j][i]
            a[j]=[x^aes_product(c,y)for x,y in zip(a[j],a[i])]
    assert all(row[:n]==[int(i==j)for j in range(n)]for i,row in enumerate(a))
    return [row[n:]for row in a]


def fused_dynamic_keys():
    aes=(Path(__file__).resolve().parent/'noid_aes_tables.h').read_text()
    rounds=load_array((Path(__file__).resolve().parent/'noid_tables.h').read_text(),'noid_round')
    f2new=load_array(aes,'f2new')
    four=affine_coefficient('NOID_AES_MUL_FOUR')
    matrix=[[four^1,1,1,1],[four,1,1,0],[1,four^1,1,1],[1,four,0,1]]
    inv=fused_dynamic_inverse(matrix)
    def dot(a,b):
        out=0
        for x,y in zip(a,b):out^=aes_product(x,y)
        return out
    rows=[]
    for r in FUSED_DYNAMIC_ROUNDS:
        key=[apply_basis(f2new,rounds[66*j+r]).to_bytes(16,'little')for j in range(4)]
        transformed=[[dot(row,column)for column in zip(*key)]for row in inv]
        recovered=[[dot(row,col)for col in zip(*transformed)]for row in matrix]
        assert recovered==[list(k)for k in key]
        rows.append([byte*0x01010101 for row in transformed for byte in row])
    out=['/* Generated exact dynamic-key transform. */','static const uint32_t fused_external_keys[5][64] = {']
    for r,row in zip(FUSED_DYNAMIC_ROUNDS,rows):
        out.append(f'    /* round {r} */ {{')
        for i in range(0,64,8):out.append('        '+', '.join(f'UINT32_C(0x{x:08x})'for x in row[i:i+8])+',')
        out.append('    },')
    out.append('};')
    return '\n'.join(out)+'\n'


def fused_dynamic_kernel():
    p=TowerProgram(False,True)
    p.inputs.update({f'b{j}_{i}':f'{64*((j-3)*16+i)}(%rdi)'for j in range(3)for i in range(16)})
    p.inputs.update({'m30':'noid_fused_matrix(%rip)','m31':'noid_fused_matrix+8(%rip)'})
    p.inputs.update({f'k{i}':f'{4*i}(%rsi){{1to16}}'for i in range(64)})
    AFFINE_MULTIPLIERS.update({'m30':affine_coefficient('NOID_AES_MUL_TWO'),
                                    'm31':affine_coefficient('NOID_AES_MUL_FOUR')})
    p.seventh()
    original=p.ops[:];rewritten=[]
    for op,dest,args in original:
        if op!='store':
            rewritten.append((op,dest,args));continue
        start=len(p.ops)
        f3,offset=args;i=offset//64
        f0,f1,f2=(f'b{j}_{i}'for j in range(3))
        u=p.emit('keyed_xor',f0,f1,i)
        v=p.emit('keyed_xor',f2,f3,16+i)
        b2=p.emit('xor',p.emit('linear',f1,'m30'),f'k{32+i}')
        d2=p.emit('xor',p.emit('linear',f3,'m30'),f'k{48+i}')
        r1=p.emit('xor3',p.emit('linear',u,'m31'),b2,v)
        r3=p.emit('xor3',p.emit('linear',v,'m31'),d2,u)
        r0=p.emit('xor3',d2,u,r1)
        r2=p.emit('xor3',b2,v,r3)
        for j,value in enumerate((r0,r1,r2,r3)):p.emit('store',value,64*((j-3)*16+i))
        rewritten.extend(p.ops[start:])
    p.ops=rewritten
    return Assembly(p).generate().replace('noid_seventh_prekeyed_asm','noid_seventh_external_dynamic_asm')




if __name__ == "__main__":
    import sys
    if "--fused-keys" in sys.argv:
        print(fused_dynamic_keys(), end="")
        raise SystemExit(0)
    for mixed, prekeyed in ((False, False), (False, True), (True, False)):
        globals().update(PARTIAL_ROUND_TUNING if mixed else FULL_ROUND_TUNING)
        program = TowerProgram(mixed, prekeyed)
        program.seventh()
        print(Assembly(program).generate(), end="")
    globals().update(FULL_ROUND_TUNING)
    program = TowerProgram(False, True, True)
    program.seventh()
    print(Assembly(program).generate(), end="")
    external_keyed_assembly()
    external_sparse4_assembly()
    print(fused_dynamic_kernel(), end="")
