"""
Fake `sqlalchemy` + `epo.tipdata.patstat` for offline runs of the notebooks.

- Models accept ONLY the real PATSTAT Global column names (a wrong attribute raises AttributeError, as on TIP).
- Queries are recorded and `patstat.df(query)` evaluates them on in-memory pandas tables: joins on shared keys,
  filters (==, !=, <, >, between, in_, like on lower/replace, isnot), group_by with min/count, distinct, union.
It is a test double, not SQL: enough to execute every query path of the notebooks on synthetic data.
"""
import re
import sys
import types

import numpy as np
import pandas as pd

COLUMNS = {
    "TLS201_APPLN": "appln_id appln_auth appln_nr appln_kind appln_filing_date appln_filing_year appln_nr_epodoc "
                    "appln_nr_original ipr_type receiving_office internat_appln_id int_phase reg_phase nat_phase "
                    "earliest_filing_date earliest_filing_year earliest_filing_id earliest_publn_date earliest_publn_year "
                    "earliest_pat_publn_id granted docdb_family_id inpadoc_family_id docdb_family_size "
                    "nb_citing_docdb_fam nb_applicants nb_inventors",
    "TLS202_APPLN_TITLE": "appln_id appln_title_lg appln_title",
    "TLS203_APPLN_ABSTR": "appln_id appln_abstract_lg appln_abstract",
    "TLS206_PERSON": "person_id person_name person_name_orig_lg person_address person_ctry_code nuts nuts_level "
                     "doc_std_name_id doc_std_name psn_id psn_name psn_level psn_sector han_id han_name han_harmonized",
    "TLS207_PERS_APPLN": "person_id appln_id applt_seq_nr invt_seq_nr",
    "TLS209_APPLN_IPC": "appln_id ipc_class_symbol ipc_class_level ipc_version ipc_value ipc_position ipc_gener_auth",
    "TLS211_PAT_PUBLN": "pat_publn_id publn_auth publn_nr publn_nr_original publn_kind appln_id publn_date publn_lg "
                        "publn_first_grant publn_claims",
    "TLS212_CITATION": "pat_publn_id citn_replenished citn_id citn_origin cited_pat_publn_id cited_appln_id "
                       "pat_citn_seq_nr cited_npl_publn_id npl_citn_seq_nr citn_gener_auth",
    "TLS215_CITN_CATEG": "pat_publn_id citn_replenished citn_id citn_categ relevant_claim",
    "TLS224_APPLN_CPC": "appln_id cpc_class_symbol",
    "TLS228_DOCDB_FAM_CITN": "docdb_family_id cited_docdb_family_id",
    "TLS230_APPLN_TECHN_FIELD": "appln_id techn_field_nr weight",
    "TLS231_INPADOC_LEGAL_EVENT": "event_id appln_id event_seq_nr event_type event_auth event_code event_filing_date "
                                  "event_publn_date event_effective_date event_text party_type party_seq_nr party_new "
                                  "party_old fee_country fee_payment_date fee_renewal_year fee_text lapse_country "
                                  "lapse_date lapse_text event_category_code event_category_title",
    "TLS803_LEGAL_EVENT_CODE": "event_auth event_code event_impact event_descr event_descr_orig event_category_code "
                               "event_category_title",
    "TLS901_TECHN_FIELD_IPC": "ipc_maingroup_symbol techn_field_nr techn_sector techn_field",
}
TABLES = {}          # model name -> DataFrame, filled by the test
JOIN_KEYS = ["appln_id", "person_id", "pat_publn_id"]


# ------------------------------------------------------------------ expressions
class Expr:
    def __init__(self, fn, label=None, agg=None, cols=()):
        self.fn, self.label_, self.agg, self.cols = fn, label, agg, set(cols)

    def label(self, name):
        return Expr(self.fn, name, self.agg, self.cols)

    def _bin(self, other, op):
        o = other if isinstance(other, Expr) else Expr(lambda d, v=other: v)
        return Expr(lambda d: op(self.fn(d), o.fn(d)), cols=self.cols | o.cols)

    __eq__ = lambda s, o: s._bin(o, lambda a, b: a == b)
    __ne__ = lambda s, o: s._bin(o, lambda a, b: a != b)
    __lt__ = lambda s, o: s._bin(o, lambda a, b: a < b)
    __gt__ = lambda s, o: s._bin(o, lambda a, b: a > b)
    __le__ = lambda s, o: s._bin(o, lambda a, b: a <= b)
    __ge__ = lambda s, o: s._bin(o, lambda a, b: a >= b)
    __and__ = lambda s, o: s._bin(o, lambda a, b: a & b)
    __or__ = lambda s, o: s._bin(o, lambda a, b: a | b)
    __mul__ = lambda s, o: s._bin(o, lambda a, b: a * b)
    __truediv__ = lambda s, o: s._bin(o, lambda a, b: a / b)
    __hash__ = object.__hash__

    def between(self, lo, hi):
        return Expr(lambda d: self.fn(d).between(lo, hi), cols=self.cols)

    def in_(self, values):
        if isinstance(values, Query):
            vals = set(values.run().iloc[:, 0])
        else:
            vals = set(values)
        return Expr(lambda d: self.fn(d).isin(vals), cols=self.cols)

    def notin_(self, values):
        e = self.in_(values)
        return Expr(lambda d: ~e.fn(d), cols=self.cols)

    def like(self, pattern):
        rx = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
        return Expr(lambda d: self.fn(d).astype(str).str.contains(rx, regex=True, na=False, flags=re.S), cols=self.cols)

    def isnot(self, v):
        return Expr(lambda d: self.fn(d).notna(), cols=self.cols)

    def desc(self):
        return self


class Col(Expr):
    def __init__(self, model, name):
        self.model, self.name = model, name
        super().__init__(lambda d, n=name: d[n], label=name, cols=[(model, name)])


class ModelMeta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name not in COLUMNS[cls.__name__].split():
            raise AttributeError(f"{cls.__name__} has no column '{name}' in PATSTAT")
        return Col(cls.__name__, name)


models = types.ModuleType("epo.tipdata.patstat.database.models")
for _name in COLUMNS:
    setattr(models, _name, ModelMeta(_name, (), {}))


# ------------------------------------------------------------------ func, or_, and_, case
class _Func:
    def __getattr__(self, name):
        if name == "replace":
            return lambda c, a, b: Expr(lambda d: c.fn(d).astype(str).str.replace(a, b, regex=False), cols=c.cols)
        if name == "lower":
            return lambda c: Expr(lambda d: c.fn(d).astype(str).str.lower(), cols=c.cols)
        if name in ("substr", "substring"):
            return lambda c, s, n: Expr(lambda d: c.fn(d).astype(str).str[s - 1:s - 1 + n], cols=c.cols)
        if name == "trim":
            return lambda c: Expr(lambda d: c.fn(d).astype(str).str.strip(), cols=c.cols)
        if name == "min":
            return lambda c: Expr(c.fn, agg="min", cols=c.cols)
        if name == "count":
            return lambda c=None: Expr(c.fn if c is not None else (lambda d: pd.Series(1, index=d.index)),
                                       agg="nunique" if c is not None and getattr(c, "distinct_", False) else "count",
                                       cols=c.cols if c is not None else [])
        if name == "distinct":
            def dist(c):
                e = Expr(c.fn, cols=c.cols)
                e.distinct_ = True
                return e
            return dist
        raise AttributeError(name)


func = _Func()


def or_(*es):
    return Expr(lambda d: np.logical_or.reduce([e.fn(d) for e in es]) if es else pd.Series(False, index=d.index),
                cols=set().union(*[e.cols for e in es]) if es else ())


def and_(*es):
    return Expr(lambda d: np.logical_and.reduce([e.fn(d) for e in es]), cols=set().union(*[e.cols for e in es]))


def case(*whens, else_=None):
    def fn(d):
        out = pd.Series(else_, index=d.index, dtype=object)
        for cond, val in reversed(whens):
            out = out.where(~cond.fn(d), val)
        return out
    return Expr(fn, cols=set().union(*[w[0].cols for w in whens]))


# ------------------------------------------------------------------ query
class Query:
    def __init__(self, cols):
        self.cols, self.joins, self.filters, self.groups, self.havings = list(cols), [], [], [], []
        self.unions, self.is_distinct = [], False

    def _copy(self):
        q = Query(self.cols)
        q.joins, q.filters, q.groups, q.havings = list(self.joins), list(self.filters), list(self.groups), list(self.havings)
        q.unions, q.is_distinct = list(self.unions), self.is_distinct
        return q

    def join(self, model, cond=None):
        q = self._copy(); q.joins.append(model.__name__ if isinstance(model, ModelMeta) else model); return q

    outerjoin = join

    def select_from(self, model):
        q = self._copy(); q.joins.insert(0, model.__name__); return q

    def filter(self, *conds):
        q = self._copy(); q.filters += list(conds); return q

    def group_by(self, *cols):
        q = self._copy(); q.groups += list(cols); return q

    def having(self, cond):
        q = self._copy(); q.havings.append(cond); return q

    def distinct(self):
        q = self._copy(); q.is_distinct = True; return q

    def union(self, *qs):
        q = self._copy(); q.unions += list(qs); return q

    def order_by(self, *a):
        return self

    def limit(self, n):
        return self

    def subquery(self):
        return self

    def select(self):
        return self

    # ---- evaluation
    def _models(self):
        ms = []
        for c in self.cols:
            for m, _ in c.cols:
                if m not in ms:
                    ms.append(m)
        for m in self.joins:
            if m not in ms:
                ms.append(m)
        for f in self.filters:
            for m, _ in f.cols:
                if m not in ms:
                    ms.append(m)
        return ms

    def run(self):
        ms = self._models()
        tab = lambda m: TABLES[m].reindex(columns=COLUMNS[m].split())   # every real column exists, empty if not faked
        d = tab(ms[0])
        todo = ms[1:]
        while todo:   # join, in turn, a table that shares a key with what is joined so far
            for m in todo:
                t = tab(m)
                keys = [k for k in JOIN_KEYS if k in d.columns and k in t.columns]
                if not keys and m == "TLS228_DOCDB_FAM_CITN":
                    keys = ["docdb_family_id"]
                if keys:
                    d = d.merge(t, on=keys[:1], how="inner", suffixes=("", f"__{m}"))
                    todo.remove(m)
                    break
            else:
                raise RuntimeError(f"cannot join {todo} to {list(d.columns)[:5]}")
        for f in self.filters:
            d = d[pd.Series(f.fn(d), index=d.index).fillna(False).astype(bool).values]
        if self.groups:
            keys = [g.label_ for g in self.groups]
            out = pd.DataFrame({g.label_: g.fn(d) for g in self.groups})
            aggs = {}
            for c in self.cols:
                if c.agg:
                    aggs[c.label_] = (c.fn(d), "nunique" if c.agg == "nunique" else c.agg)
            res = out.drop_duplicates().set_index(keys)
            for lab, (vals, how) in aggs.items():
                res[lab] = vals.groupby([out[k] for k in keys]).agg(how)
            res = res.reset_index()
            agg_cols = [c.label_ for c in self.cols if c.agg]
            for h in self.havings:
                res = res[_having(h, res, agg_cols[0])]
            out = res[[c.label_ for c in self.cols]]
        else:
            out = pd.DataFrame({(c.label_ or f"c{i}"): c.fn(d).values for i, c in enumerate(self.cols)})
        for u in self.unions:
            out = pd.concat([out, u.run().set_axis(out.columns, axis=1)], ignore_index=True)
        if self.is_distinct or self.unions:
            out = out.drop_duplicates()
        return out.reset_index(drop=True)


def _having(h, res, col):
    # the notebooks use having(year.between(...)) on the aggregated year: evaluate it on that column
    probe = res.copy()
    for m, n in h.cols:
        probe[n] = res[col]
    return h.fn(probe).astype(bool).values


class Session:
    def query(self, *cols):
        return Query(cols)


class PatstatClient:
    def __init__(self, env="TEST"):
        self.env = env

    def orm(self):
        return Session()

    def df(self, q):
        return q.run()


def install():
    sa = types.ModuleType("sqlalchemy")
    sa.func, sa.or_, sa.and_, sa.case = func, or_, and_, case
    sa.distinct = func.distinct
    sa.literal = lambda v: Expr(lambda d: v)
    for name in ["epo", "epo.tipdata", "epo.tipdata.patstat", "epo.tipdata.patstat.database"]:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["epo.tipdata.patstat"].PatstatClient = PatstatClient
    sys.modules["epo.tipdata.patstat.database"].models = models
    sys.modules["epo.tipdata.patstat.database.models"] = models
    sys.modules["sqlalchemy"] = sa
