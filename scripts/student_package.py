#!/usr/bin/env python3
"""Standalone classroom package verifier, safe MySQL importer and acceptance tests.

No business endpoints or recommendation algorithms are implemented.
"""
import argparse
import collections
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

TABLES=['movies','history_users','history_ratings','accounts','account_ratings',
        'account_favorites','recommendation_results','evaluation_results']
MOVIE_COLUMNS=['movie_id','movielens_title','title_zh','original_title','genres','release_year',
    'release_date','overview','directors','cast_top10','runtime_minutes','original_language','poster_path','tmdb_id','imdb_id']
RATE_COLUMNS=['userId','movieId','rating','timestamp']
RATING_SQL_COLUMNS=['ml_user_id','movie_id','rating','timestamp_utc']
MODULUS=2**256


def require(ok,message):
    if not ok: raise ValueError(message)


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def dump(path,obj):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def records(path,columns=None):
    with Path(path).open(encoding='utf-8',newline='') as f:
        reader=csv.reader(f);header=next(reader)
        if columns is not None: require(header==columns,f'Column mismatch: {path.name}')
        for row in reader:
            require(len(row)==len(header),f'CSV column count: {path.name}')
            yield row


def integer(value,positive=True):
    require(bool(re.fullmatch(r'[0-9]+',str(value))),'Expected an ASCII integer')
    result=int(value)
    require(result>0 if positive else result>=0,'Integer outside allowed range')
    return result


def rating(value):
    try: r=Decimal(str(value))
    except InvalidOperation as exc: raise ValueError('Invalid rating') from exc
    require(r.is_finite() and Decimal('.5')<=r<=Decimal('5') and r*2==(r*2).to_integral_value(),
            'Rating must be 0.5..5.0 in exact half-star steps')
    return r


def movie_tuple(row):
    result=[None if x=='' else x for x in row]
    for idx in [0,5,10,13]:
        if result[idx] is not None: result[idx]=integer(result[idx])
    require(result[1] is not None and result[4] is not None,'Movie title/genres missing')
    if result[5] is not None: require(1000<=result[5]<=9999,'Invalid title-derived year')
    for idx in [8,9]:
        if result[idx] is not None: result[idx]=json.dumps(json.loads(result[idx]),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return tuple(result)


def rating_tuple(row):
    u,m=integer(row[0]),integer(row[1]);r=rating(row[2]);t=integer(row[3],False)
    require(t<=253402300799,'Invalid UTC timestamp')
    return u,m,r,t


def canonical(row):
    result=[]
    for value in row:
        if isinstance(value,Decimal): value=f'{value:.1f}'
        elif hasattr(value,'isoformat'): value=value.isoformat()
        result.append(value)
    return json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()


class Fingerprint:
    def __init__(self): self.count=0;self.total=0
    def add(self,row):
        self.count+=1;self.total=(self.total+int.from_bytes(hashlib.sha256(canonical(row)).digest(),'big'))%MODULUS
    def value(self): return {'records':self.count,'row_sha256_sum_mod_2p256':f'{self.total:064x}'}


def offline(package):
    start=time.monotonic();package=package.resolve()
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    require(manifest['schema_version']=='student-eight-v3','Unknown schema version')
    for relative,meta in manifest['files'].items():
        path=(package/relative).resolve()
        require(path.is_relative_to(package),'Unsafe manifest path')
        require(path.is_file() and file_sha(path)==meta['sha256'],f'File hash mismatch: {relative}')
    movies={};movie_fp=Fingerprint()
    for row in records(package/'movies.csv',MOVIE_COLUMNS):
        parsed=movie_tuple(row);require(parsed[0] not in movies,'Duplicate movieId')
        movies[parsed[0]]=parsed;movie_fp.add(parsed)
    users={};user_fp=Fingerprint()
    for uid,role in records(package/'history_users.csv',['ml_user_id','evaluation_role']):
        uid=integer(uid);require(uid not in users and role in ['warm','cold'],'Duplicate/invalid history user')
        users[uid]=role;user_fp.add((uid,role))
    require(len(movies)==manifest['movies'] and len(users)==manifest['history_users'],'Catalog/user count mismatch')
    fingerprints={};partition_pairs={};train_support=collections.Counter();user_times=collections.defaultdict(dict)
    for name in ['train','validation','test','cold-history']:
        fp=Fingerprint();pairs=set()
        for row in records(package/f'evaluation/{name}.csv',RATE_COLUMNS):
            u,m,r,t=rating_tuple(row)
            require(u in users and m in movies,'Rating foreign key missing')
            require((u,m) not in pairs,'Duplicate rating primary key')
            require((users[u]=='cold')==(name=='cold-history'),'Cold/warm user isolation failed')
            pairs.add((u,m));fp.add((u,m,r,t));user_times[u].setdefault(name,[]).append(t)
            if name=='train': train_support[m]+=1
        fingerprints[name]=fp.value();partition_pairs[name]=pairs
    for i,name in enumerate(partition_pairs):
        for other in list(partition_pairs)[i+1:]: require(partition_pairs[name].isdisjoint(partition_pairs[other]),'Split overlap')
    for uid,parts in user_times.items():
        if users[uid]=='cold': continue
        require(parts.get('train'),'Warm user missing train')
        if parts.get('validation'): require(max(parts['train'])<min(parts['validation']),'Train/validation time leak')
        if parts.get('test'): require(max(parts['train']+parts.get('validation',[]))<min(parts['test']),'Train/test time leak')
    require(set(train_support)==set(movies) and min(train_support.values())>=5,'Training catalog support failed')
    require(dict((integer(m),integer(n)) for m,n in records(package/'training-catalog.csv',['movie_id','train_support']))==dict(train_support),'Catalog evidence mismatch')
    full_fp=Fingerprint();full_pairs=set()
    for row in records(package/'business/history_ratings.csv',RATE_COLUMNS):
        parsed=rating_tuple(row);u,m,_,_=parsed
        require(u in users and m in movies and (u,m) not in full_pairs,'Business rating primary/foreign key failed')
        full_pairs.add((u,m));full_fp.add(parsed)
    require(full_pairs==set.union(*partition_pairs.values()),'Partition union differs from business history')
    # Multiset fingerprints also catch different rating/timestamp values for the same key.
    total=sum(int(v['row_sha256_sum_mod_2p256'],16) for v in fingerprints.values())%MODULUS
    require(full_fp.count==sum(v['records'] for v in fingerprints.values()) and full_fp.total==total,'Partition values differ')
    cold_checks={}
    for k in [0,1,5,10]:
        support=collections.defaultdict(dict);qcount=0
        for row in records(package/f'evaluation/cold-k{k}-support.csv',RATE_COLUMNS):
            u,m,r,t=rating_tuple(row);require(users.get(u)=='cold' and m in movies,'Cold support foreign key')
            require((u,m) in partition_pairs['cold-history'] and m not in support[u],'Cold support membership/duplicate')
            support[u][m]=t
        for row in records(package/f'evaluation/cold-k{k}-query.csv',RATE_COLUMNS):
            u,m,r,t=rating_tuple(row);require(users.get(u)=='cold' and m in movies,'Cold query foreign key')
            require((u,m) in partition_pairs['cold-history'] and m not in support[u],'Cold support/query overlap')
            if support[u]: require(t>max(support[u].values()),'Cold query time leak')
            qcount+=1
        require(all(len(p)<=k for p in support.values()),'More than nominal k support')
        cold_checks[str(k)]={'support':sum(len(p) for p in support.values()),'query':qcount}
    expected={'movies':movie_fp.value(),'history_users':user_fp.value(),
        'evaluation_ratings':fingerprints['train'],'business_ratings':full_fp.value()}
    for mode in manifest['modes']:
        require(manifest['modes'][mode]['ratings']==expected[mode+'_ratings']['records'],'Manifest rating count differs')
    return {'status':'passed','dataset_version':manifest['dataset_version'],'manifest_sha256':file_sha(package/'manifest.json'),
        'checked_files':len(manifest['files']),'movies':len(movies),'history_users':len(users),
        'training_support_min':min(train_support.values()),'cold_protocols':cold_checks,
        'fingerprints':expected,'elapsed_seconds':round(time.monotonic()-start,3)},manifest


def sql_statements(path):
    # Controlled DDL/query files: no procedures, quoted semicolons or dynamic data.
    text='\n'.join(line for line in path.read_text(encoding='utf-8').splitlines() if not line.lstrip().startswith('--'))
    return [s.strip() for s in text.split(';') if s.strip()]


def connect(args):
    import pymysql
    options={'host':os.getenv('MYSQL_HOST','127.0.0.1'),'port':int(os.getenv('MYSQL_PORT','3306')),
        'user':os.getenv('MYSQL_USER','root'),'password':os.getenv('MYSQL_PASSWORD',''),
        'charset':'utf8mb4','autocommit':False,'connect_timeout':10,'read_timeout':120,'write_timeout':120,
        'local_infile':False}
    if os.getenv('MYSQL_UNIX_SOCKET'): options['unix_socket']=os.environ['MYSQL_UNIX_SOCKET']
    if os.getenv('MYSQL_SSL_CA'):
        options.update(ssl_ca=os.environ['MYSQL_SSL_CA'],ssl_verify_cert=True,ssl_verify_identity=True)
    conn=pymysql.connect(**options)
    with conn.cursor() as cur:
        cur.execute('SELECT VERSION()');version=cur.fetchone()[0]
        match=re.match(r'8\.(\d+)\.(\d+)',version)
        require(match is not None and 'mariadb' not in version.lower(),'Requires real MySQL 8, not MariaDB or a substitute')
        require(int(match.group(1))>0 or int(match.group(2))>=16,'MySQL 8.0.16+ CHECK enforcement required')
        cur.execute("SET SESSION time_zone='+00:00'")
        cur.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")
    return conn,version


def database_name(args,manifest,identity):
    default='rec_course_v3_'+identity[:10]+'_'+args.mode
    name=args.database or default
    require(len(name)<=64 and re.fullmatch(r'rec_course_[a-z0-9_]+_'+args.mode,name) is not None,
        'Database must use isolated rec_course_..._<mode> name; existing business names are never accepted')
    return name


def receipt(identity,mode): return 'course-v3:ready:'+mode+':'+identity


def execute_many(cur,table,columns,values):
    statement='INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join(['%s']*len(columns))+')'
    batch=[]
    for value in values:
        batch.append(value)
        if len(batch)>=2000: cur.executemany(statement,batch);batch=[]
    if batch: cur.executemany(statement,batch)


def db_verify(conn,package,mode,expected):
    counts={};fps={}
    selections={'movies':MOVIE_COLUMNS,'history_users':['ml_user_id','evaluation_role'],'history_ratings':RATING_SQL_COLUMNS}
    import pymysql
    with conn.cursor() as cur:
        cur.execute('SHOW TABLES');require({r[0] for r in cur.fetchall()}==set(TABLES),'Database must contain exactly eight expected tables')
        for table in TABLES:
            cur.execute('SELECT COUNT(*) FROM '+table);counts[table]=cur.fetchone()[0]
    for table,columns in selections.items():
        fp=Fingerprint()
        with conn.cursor(pymysql.cursors.SSCursor) as cur:
            cur.execute('SELECT '+','.join(columns)+' FROM '+table)
            for row in cur:
                if table=='movies':
                    row=list(row)
                    for i in [8,9]:
                        if row[i] is not None:row[i]=json.dumps(json.loads(row[i]),ensure_ascii=False,sort_keys=True,separators=(',',':'))
                fp.add(row)
        key=table if table!='history_ratings' else mode+'_ratings'
        require(fp.value()==expected['fingerprints'][key],f'Database content mismatch: {table}')
        fps[table]=fp.value()
    checks={}
    with conn.cursor() as cur:
        for statement in sql_statements(package/'sql/verify.sql'):
            cur.execute(statement)
            name,value=cur.fetchone();checks[name]=int(value)
            require(value==0,'Database integrity check failed: '+name)
        cur.execute("SELECT COUNT(*) FROM history_ratings r JOIN history_users u ON r.ml_user_id=u.ml_user_id WHERE u.evaluation_role='cold'")
        cold_train=cur.fetchone()[0]
        if mode=='evaluation':require(cold_train==0,'Cold history leaked into evaluation train table')
    return {'status':'passed','counts':counts,'immutable_table_fingerprints':fps,'integrity_checks':checks,
        'cold_user_ratings_in_history_table':cold_train}


def import_package(args,checked,manifest):
    conn,server=connect(args);identity=checked['manifest_sha256'];db=database_name(args,manifest,identity)
    lock='course-import-'+hashlib.sha256(db.encode()).hexdigest()[:40]
    result={'database':db,'mode':args.mode,'server_version':server,'manifest_sha256':identity}
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT GET_LOCK(%s,10)',(lock,));require(cur.fetchone()[0]==1,'Import lock busy')
            cur.execute('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s',(db,));exists=cur.fetchone()[0]
            if not exists:cur.execute('CREATE DATABASE `'+db+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin')
            conn.select_db(db)
            cur.execute('SHOW TABLES');tables=[r[0] for r in cur.fetchall()]
            if tables:
                cur.execute("SELECT table_comment FROM information_schema.tables WHERE table_schema=%s AND table_name='history_ratings'",(db,))
                row=cur.fetchone()
                require(row is not None and row[0]==receipt(identity,args.mode),
                    'Refusing nonempty/unrecognized/unfinished database; choose a new empty isolated database')
                result.update(action='verified_noop',verification=db_verify(conn,args.package,args.mode,checked));return result
            for statement in sql_statements(args.package/f'sql/schema_{args.mode}.sql'):cur.execute(statement)
            conn.begin()
            execute_many(cur,'movies',MOVIE_COLUMNS,(movie_tuple(r) for r in records(args.package/'movies.csv',MOVIE_COLUMNS)))
            execute_many(cur,'history_users',['ml_user_id','evaluation_role'],
                ((integer(u),role) for u,role in records(args.package/'history_users.csv',['ml_user_id','evaluation_role'])))
            execute_many(cur,'history_ratings',RATING_SQL_COLUMNS,
                (rating_tuple(r) for r in records(args.package/manifest['modes'][args.mode]['ratings_file'],RATE_COLUMNS)))
            # Verification before commit; failed imports roll all data back.
            verified=db_verify(conn,args.package,args.mode,checked)
            conn.commit()
            cur.execute('ALTER TABLE history_ratings COMMENT=%s',(receipt(identity,args.mode),))
            result.update(action='imported',verification=verified)
            return result
    except Exception:
        conn.rollback();raise
    finally:
        try:
            with conn.cursor() as cur:cur.execute('SELECT RELEASE_LOCK(%s)',(lock,))
        finally:conn.close()


def acceptance(args,checked,manifest):
    conn,server=connect(args);db=database_name(args,manifest,checked['manifest_sha256']);conn.select_db(db)
    tests=[]
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_comment FROM information_schema.tables WHERE table_schema=%s AND table_name='history_ratings'",(db,))
            require(cur.fetchone()[0]==receipt(checked['manifest_sha256'],args.mode),'Acceptance only permitted on a verified course database')
            cur.execute('SELECT movie_id FROM movies ORDER BY movie_id LIMIT 2');m1,m2=[r[0] for r in cur.fetchall()]
            cur.execute("SELECT ml_user_id FROM history_users WHERE evaluation_role='warm' ORDER BY ml_user_id LIMIT 1");uid=cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM accounts');require(cur.fetchone()[0]==0,'Acceptance requires no real business accounts')
            conn.begin()
            def rejected(name,sql,params,code):
                cur.execute('SAVEPOINT acceptance_case')
                try:cur.execute(sql,params)
                except Exception as exc:
                    require(exc.args[0]==code,f'{name}: expected MySQL error {code}, got {exc.args[0]}')
                    tests.append({'name':name,'status':'passed','mysql_error':code})
                else:raise ValueError(name+': invalid data unexpectedly accepted')
                finally:cur.execute('ROLLBACK TO SAVEPOINT acceptance_case')
            # Explicit test-only fixture, always rolled back. It is not a movie dataset record.
            fixture=2**63-1;text='验收专用🙂 中文, "引号" \\ 反斜线\n第二行 café'
            cur.execute('INSERT INTO movies(movie_id,movielens_title,genres) VALUES(%s,%s,%s)',(fixture,text,'验收专用'))
            cur.execute('SELECT movielens_title FROM movies WHERE movie_id=%s',(fixture,))
            require(cur.fetchone()[0]==text,'Unicode/special-character roundtrip failed')
            tests.append({'name':'chinese_emoji_quotes_newline_backslash_roundtrip','status':'passed'})
            for value in ['0.0','5.5','4.1']:
                rejected('rating_reject_'+value,'INSERT INTO history_ratings VALUES(%s,%s,%s,%s)',(uid,fixture,value,1),3819)
            rejected('foreign_movie_rejected','INSERT INTO history_ratings VALUES(%s,%s,%s,%s)',(uid,fixture-1,'4.0',1),1452)
            rejected('foreign_user_rejected','INSERT INTO history_ratings VALUES(%s,%s,%s,%s)',(fixture,m1,'4.0',1),1452)
            cur.execute('SELECT ml_user_id,movie_id,rating,timestamp_utc FROM history_ratings LIMIT 1');existing=cur.fetchone()
            rejected('duplicate_rating_pk','INSERT INTO history_ratings VALUES(%s,%s,%s,%s)',existing,1062)
            for value in ['4.01','NaN','Infinity']:
                try:rating(value)
                except ValueError:tests.append({'name':'python_exact_rating_reject_'+value,'status':'passed'})
                else:raise ValueError('Exact rating pre-validation failed')
            if args.mode=='business':
                cur.execute('INSERT INTO accounts(account_id,login_name,password_hash) VALUES(%s,%s,%s)',(uid,'验收账号🙂','test-only-not-a-real-password-hash'))
                cur.execute('SELECT account_id FROM accounts WHERE account_id=%s',(uid,));require(cur.fetchone()[0]==uid,'Account namespace test')
                tests.append({'name':'same_number_app_and_history_user_are_isolated','status':'passed'})
                rejected('account_rating_range','INSERT INTO account_ratings(account_id,movie_id,rating) VALUES(%s,%s,%s)',(uid,m1,'5.5'),3819)
                rejected('account_favorite_foreign_key','INSERT INTO account_favorites(account_id,movie_id) VALUES(%s,%s)',(uid,fixture-1),1452)
            else:
                rejected('evaluation_database_forbids_business_accounts','INSERT INTO accounts(login_name,password_hash) VALUES(%s,%s)',('test','fixture'),3819)
            columns='batch_token,runtime_mode,dataset_version,split_version,algorithm,model_version,ml_user_id,account_id,movie_id,rank_pos'
            sql='INSERT INTO recommendation_results('+columns+') VALUES('+','.join(['%s']*10)+')'
            values=['acceptance',args.mode,manifest['dataset_version'],manifest['split_version'],'diagnostic-fixture','fixture-model-v1',uid,None,m1,1]
            cur.execute(sql,values)
            cur.execute('SELECT batch_id,subject_key FROM recommendation_results WHERE batch_token=%s',('acceptance',));batch,key=cur.fetchone()
            expected_batch=hashlib.sha256('|'.join([args.mode,manifest['dataset_version'],manifest['split_version'],'diagnostic-fixture','fixture-model-v1','acceptance','ml']).encode()).hexdigest()
            require(batch==expected_batch and key=='ml:'+str(uid),'Batch/model binding failed')
            tests.append({'name':'batch_id_binds_dataset_split_model_mode_subject_kind','status':'passed'})
            other=values.copy();other[8]=m2
            rejected('recommendation_duplicate_rank',sql,other,1062)
            other=values.copy();other[9]=2
            rejected('recommendation_duplicate_movie',sql,other,1062)
            other=values.copy();other[6]=None
            rejected('recommendation_missing_user',sql,other,3819)
            other=values.copy();other[1]='business' if args.mode=='evaluation' else 'evaluation'
            rejected('wrong_runtime_mode_result',sql,other,3819)
            # Different model version has a different deterministic batch ID.
            other=values.copy();other[5]='fixture-model-v2';cur.execute(sql,other)
            cur.execute('SELECT COUNT(DISTINCT batch_id) FROM recommendation_results WHERE batch_token=%s',('acceptance',))
            require(cur.fetchone()[0]==2,'Different model version reused batch ID')
            tests.append({'name':'different_model_versions_produce_different_batches','status':'passed'})
            conn.rollback()
        verified=db_verify(conn,args.package,args.mode,checked)
        require(all(verified['counts'][t]==0 for t in TABLES[3:]),'Acceptance fixtures persisted')
        return {'status':'passed','database':db,'server_version':server,'mode':args.mode,'tests':tests,
            'fixtures_rolled_back':True,'post_test_verification':verified}
    except Exception:
        conn.rollback();raise
    finally:conn.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check','import','acceptance'])
    p.add_argument('--package',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--mode',choices=['evaluation','business'],default='evaluation')
    p.add_argument('--database');p.add_argument('--report',type=Path)
    args=p.parse_args();args.package=args.package.resolve()
    try:
        checked,manifest=offline(args.package)
        if args.command=='check':result=checked
        elif args.command=='import':result=import_package(args,checked,manifest)
        else:result=acceptance(args,checked,manifest)
        if args.report:dump(args.report,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as exc:
        error={'status':'failed','error_type':type(exc).__name__,'message':str(exc)}
        if args.report:dump(args.report,error)
        print(json.dumps(error,ensure_ascii=False),file=sys.stderr);sys.exit(1)


if __name__=='__main__':main()
