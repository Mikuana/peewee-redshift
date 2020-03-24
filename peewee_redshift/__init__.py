from itertools import groupby
from operator import itemgetter
from typing import List, Tuple

# noinspection PyProtectedMember
from peewee import ColumnMetadata, ViewMetadata, ForeignKeyMetadata
from playhouse.reflection import PostgresqlDatabase, PostgresqlMetadata, UnknownField, Introspector

skip_schemas = ['information_schema', 'pg_catalog', 'pg_toast']


class RedshiftDatabase(PostgresqlDatabase):
    stash = None

    def get_tables(self, schema=None):
        if self.stash:
            return self.stash.tables.get(schema or 'public', [])
        else:
            return super().get_tables(schema)

    def get_views(self, schema=None):
        if self.stash:
            return self.stash.views.get(schema or 'public', [])
        else:
            return super().get_views(schema)

    def get_indexes(self, *args, **kwargs):
        """ Redshift has no indexes, therefore returns no indexes. """
        return []

    def get_columns(self, table, schema=None):
        if self.stash:
            return self.stash.columns.get(schema or 'public', {}).get(table, [])
        else:
            return super().get_columns(table, schema)

    def get_primary_keys(self, table, schema=None):
        if self.stash:
            return self.stash.primary_keys.get(schema or 'public', {}).get(table, [])
        else:
            return super().get_primary_keys(table, schema)

    def get_foreign_keys(self, table, schema=None):
        if self.stash:
            return self.stash.foreign_keys.get(schema or 'public', {}).get(table, [])
        else:
            return super().get_foreign_keys(table, schema)


class RedshiftMetadata(PostgresqlMetadata):
    def __init__(self, database: RedshiftDatabase):
        super().__init__(database)

    def get_column_types(self, table, schema):
        if self.database.stash:
            return self.database.stash.column_types.get(schema or 'public', {}).get(table, []), {}
        else:
            return super().get_column_types(table, schema)


class RedshiftStash:
    def __init__(self, db, schemas: List[str] = None, tables: List[str] = None):
        self.db = db
        self.schema_filter = schemas
        self.table_filter = tables

        self.tables = self.stash_tables()
        self.views = self.stash_views()
        self.primary_keys = self.stash_primary_keys()
        self.foreign_keys = self.stash_foreign_keys()
        self.columns = self.stash_columns()
        self.column_types = self.stash_column_types()

    def where_schema_parm(self, schema_column) -> Tuple[str, tuple]:
        if self.schema_filter:
            where, params = f"{schema_column} IN %s", (tuple(self.schema_filter),)
        else:
            where, params = f"{schema_column} NOT IN %s", (tuple(skip_schemas),)
        return where, params

    def where_table_parm(self, schema_column, table_column) -> Tuple[str, tuple]:
        where, params = self.where_schema_parm(schema_column)
        if self.table_filter:
            where += f" AND {table_column} IN %s"
            params = params + tuple(self.table_filter)
        return where, params

    def stash_tables(self):
        where, params = self.where_schema_parm('schemaname')

        query = ('SELECT schemaname, tablename FROM pg_catalog.pg_tables '
                 f'WHERE {where} ORDER BY schemaname, tablename')

        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        tables = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            tables[schema] = [tn for (sn, tn) in list(g1)]
        return tables

    def stash_views(self):
        where, params = self.where_schema_parm('schemaname')
        query = ('SELECT schemaname, viewname, definition FROM pg_catalog.pg_views '
                 f'WHERE {where} ORDER BY schemaname, viewname')
        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        views = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            views[schema] = [ViewMetadata(vn, sql.strip(' \t;')) for (sn, vn, sql) in list(g1)]
        return views

    def stash_primary_keys(self):
        where, params = self.where_table_parm('tc.table_schema', 'tc.table_name')
        query = (
            "SELECT tc.table_schema, tc.table_name, kc.column_name "
            "FROM information_schema.table_constraints AS tc "
            "INNER JOIN information_schema.key_column_usage AS kc "
            "ON tc.table_name = kc.table_name "
            "AND tc.table_schema = kc.table_schema "
            "AND tc.constraint_name = kc.constraint_name "
            f"WHERE tc.constraint_type = 'PRIMARY KEY' AND {where}"
        )
        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        primary_keys = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            primary_keys[schema] = {}
            for table, g2 in groupby(g1, key=itemgetter(1)):
                primary_keys[schema][table] = [cn for (sn, tn, cn) in list(g2)]
        return primary_keys

    def stash_foreign_keys(self):
        where, params = self.where_table_parm('kcu.table_schema', 'kcu.table_name')
        query = (
            "SELECT DISTINCT "
            "kcu.table_schema, kcu.table_name, kcu.column_name, ccu.table_schema, ccu.table_name, ccu.column_name "
            "FROM information_schema.table_constraints AS tc "
            "JOIN information_schema.key_column_usage AS kcu "
            "ON tc.constraint_name = kcu.constraint_name "
            "AND tc.constraint_schema = kcu.constraint_schema "
            "JOIN information_schema.constraint_column_usage AS ccu "
            "ON ccu.constraint_name = tc.constraint_name "
            "AND ccu.constraint_schema = tc.constraint_schema "
            f"WHERE tc.constraint_type = 'FOREIGN KEY' AND {where}"
        )
        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        foreign_keys = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            foreign_keys[schema] = {}
            for table, g2 in groupby(g1, key=itemgetter(1)):
                foreign_keys[schema][table] = [
                    ForeignKeyMetadata(fc, pt, pc, ft)
                    for (fs, ft, fc, ps, pt, pc) in list(g2)
                ]
        return foreign_keys

    def stash_columns(self):
        where, params = self.where_table_parm('table_schema', 'table_name')
        query = (
            "SELECT table_schema, table_name, column_name, is_nullable, data_type, column_default "
            "FROM information_schema.columns tc "
            f"WHERE {where} "
            "ORDER BY table_schema, table_name, ordinal_position"
        )
        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        columns = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            columns[schema] = {}
            for table, g2 in groupby(g1, key=itemgetter(1)):
                columns[schema][table] = [
                    ColumnMetadata(cn, dt, nl == 'YES', cn in self.primary_keys.get(sn, {}).get(tn, []), tn, cd)
                    for (sn, tn, cn, nl, dt, cd) in list(g2)
                ]
        return columns

    def stash_column_types(self):
        where, params = self.where_table_parm('pgn.nspname', 'pgc.relname')
        query = (
            'SELECT pgn.nspname, pgc.relname, attname, atttypid '
            'FROM pg_catalog.pg_attribute pga '
            'INNER JOIN pg_catalog.pg_class pgc ON pga.attrelid = pgc.oid '
            'INNER JOIN pg_catalog.pg_namespace pgn ON pgc.relnamespace = pgn.oid '
            f'WHERE {where} AND attnum > 0 AND reltype != 0'
        )
        cursor = self.db.execute_sql(query, params)
        fetch = cursor.fetchall()
        column_types = {}
        for schema, g1 in groupby(fetch, key=itemgetter(0)):
            column_types[schema] = {}
            for table, g2 in groupby(g1, key=itemgetter(1)):
                column_types[schema][table] = {
                    cn: RedshiftMetadata.column_map.get(at, UnknownField) for (sn, tn, cn, at) in list(g2)
                }
        return column_types


class RedshiftIntrospector(Introspector):
    @classmethod
    def from_database(cls, database, schema=None):
        if isinstance(database, RedshiftDatabase):
            database.stash = RedshiftStash(database, schemas=schema)
            return cls(RedshiftMetadata(database), schema=schema)
        else:
            return super().from_database(database, schema)


def generate_models(database, schema=None, **options):
    introspector = RedshiftIntrospector.from_database(database, schema=schema)
    return introspector.generate_models(**options)
