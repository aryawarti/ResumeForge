"""Vocabulary used by the validation guards.

Two principles shape this file.

First, recall beats precision. A technology the extractor misses is a
technology that can be silently invented; a technology it over-detects merely
causes a legitimate rewrite to be rejected. The former breaks the product's
central promise, the latter costs one rephrasing, so the lists lean inclusive
and the heuristics lean aggressive.

Second, the scope vocabulary exists because a technology check alone does not
catch the failure mode people actually get burned by. "Built the payments
service" becoming "Led development of the payments service" adds no
technology and no number, and is exactly the claim that collapses in a
screening call.
"""

from __future__ import annotations

__all__ = [
    "TECHNOLOGIES",
    "TECH_ALIASES",
    "SCOPE_TERMS",
    "SCOPE_PATTERNS",
    "STOPWORD_CAPS",
]

# --- Technologies ---------------------------------------------------------
# Grouped only for readability; all groups are flattened on import.

_LANGUAGES = """
python java javascript typescript go golang rust ruby php swift kotlin scala
c c++ cpp c# csharp objective-c perl r matlab julia haskell elixir erlang
clojure lua dart groovy bash shell powershell sql plpgsql tsql assembly
fortran cobol vba solidity zig ocaml f# racket scheme prolog
""".split()

_WEB = """
react angular vue svelte nextjs next.js nuxt remix astro jquery backbone ember
redux mobx rxjs zustand tailwind bootstrap sass scss less webpack vite rollup
esbuild parcel babel eslint prettier storybook htmx alpine.js
html css html5 css3 webassembly wasm
""".split()

_BACKEND = """
django flask fastapi tornado pyramid bottle sanic starlette
express nestjs koa hapi fastify
spring springboot hibernate quarkus micronaut vertx
rails sinatra laravel symfony codeigniter cakephp
gin echo fiber chi
asp.net dotnet .net entityframework
graphql grpc rest soap trpc openapi swagger protobuf thrift avro
""".split()

_DATA = """
postgres postgresql mysql mariadb sqlite oracle sqlserver mssql db2
mongodb cassandra dynamodb couchdb couchbase neo4j arangodb
redis memcached etcd consul zookeeper
elasticsearch opensearch solr lucene meilisearch typesense algolia
snowflake redshift bigquery databricks clickhouse duckdb presto trino athena
hive hbase druid pinot
kafka rabbitmq activemq pulsar nats sqs sns kinesis eventbridge
airflow dagster prefect luigi dbt spark hadoop flink beam storm samza
pandas numpy scipy polars dask ray modin pyarrow
""".split()

_ML = """
tensorflow pytorch keras jax theano mxnet caffe
scikit-learn sklearn xgboost lightgbm catboost statsmodels
huggingface transformers langchain langgraph llamaindex spacy nltk gensim
opencv pillow yolo detectron mlflow kubeflow sagemaker vertexai wandb
bert gpt llm rag embeddings onnx tensorrt triton vllm
pinecone weaviate qdrant chroma milvus faiss pgvector
""".split()

_CLOUD = """
aws azure gcp ec2 s3 lambda ecs eks fargate rds aurora cloudfront route53
cloudformation cloudwatch iam vpc elasticache emr glue stepfunctions appsync
amplify cognito secretsmanager
gke gce cloudrun cloudfunctions pubsub firestore firebase spanner dataflow
aks cosmosdb appservice functions blobstorage
heroku render vercel netlify railway fly.io digitalocean linode cloudflare
supabase planetscale neon
""".split()

_INFRA = """
docker kubernetes k8s helm istio linkerd envoy nginx apache haproxy traefik
caddy terraform pulumi ansible chef puppet saltstack vagrant packer
jenkins circleci travis gitlab github githubactions bamboo teamcity argocd
flux spinnaker drone buildkite
prometheus grafana datadog newrelic splunk sentry elk logstash kibana fluentd
opentelemetry jaeger zipkin pagerduty nagios zabbix
git svn mercurial subversion
linux unix ubuntu debian centos rhel alpine windows macos
nix bazel make cmake gradle maven npm yarn pnpm pip poetry uv conda cargo
""".split()

_PRACTICE = """
microservices monolith serverless soa eventdriven cqrs eventsourcing
kubernetes-native servicemesh api-gateway loadbalancing autoscaling
ci cd cicd devops sre gitops mlops dataops infrastructure-as-code
tdd bdd ddd agile scrum kanban
oauth oauth2 saml jwt sso ldap kerberos mtls tls ssl
websocket webrtc mqtt amqp sse http2 http3 quic grpc-web
""".split()

_TOOLS = """
jira confluence notion linear asana trello slack figma sketch postman insomnia
tableau powerbi looker metabase superset redash quicksight
salesforce sap servicenow stripe twilio sendgrid segment amplitude mixpanel
selenium cypress playwright puppeteer jest mocha pytest junit rspec testng
vitest karma jasmine cucumber locust jmeter k6 gatling
""".split()

TECHNOLOGIES: frozenset[str] = frozenset(
    _LANGUAGES
    + _WEB
    + _BACKEND
    + _DATA
    + _ML
    + _CLOUD
    + _INFRA
    + _PRACTICE
    + _TOOLS
)

# Surface forms that must normalise to the same canonical token, so that
# swapping one spelling for another does not read as a new technology.
TECH_ALIASES: dict[str, str] = {
    "golang": "go",
    "postgresql": "postgres",
    "psql": "postgres",
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "sklearn": "scikit-learn",
    "scikit": "scikit-learn",
    "next.js": "nextjs",
    "node.js": "nodejs",
    "node": "nodejs",
    "cpp": "c++",
    "csharp": "c#",
    "dotnet": ".net",
    "gcp": "gcp",
    "googlecloud": "gcp",
    "amazonwebservices": "aws",
    "mssql": "sqlserver",
    "gha": "githubactions",
    "github actions": "githubactions",
    "tf": "terraform",
    "es": "elasticsearch",
    "mq": "rabbitmq",
    "restful": "rest",
    "graphq": "graphql",
    "micro-services": "microservices",
    "micro services": "microservices",
    "event-driven": "eventdriven",
    "ml": "machinelearning",
    "ai": "artificialintelligence",
}

# --- Scope and seniority claims ------------------------------------------
# Verbs and phrases that assert authority, ownership or team leadership.
# A rewrite may not introduce any of these unless the original already did.
SCOPE_TERMS: frozenset[str] = frozenset(
    """
led leading leader lead spearheaded headed chaired directed
managed managing manager supervised oversaw overseeing oversight
owned owning owner
architected architecting architect
founded co-founded founding
mentored mentoring mentor coached coaching
hired recruited staffed onboarded
principal staff senior lead-engineer
drove driving championed pioneered spearheading
established initiated launched instituted
delegated coordinated orchestrated
promoted awarded recognized
""".split()
)

# Phrases matched as regexes rather than single tokens.
SCOPE_PATTERNS: tuple[str, ...] = (
    r"\bteam of \d+",
    r"\bteam of (?:two|three|four|five|six|seven|eight|nine|ten)\b",
    r"\b\d+\s*(?:direct reports|engineers|developers|people)\b",
    r"\bcross[- ]functional\b",
    r"\bfrom scratch\b",
    r"\bground up\b",
    r"\bend[- ]to[- ]end ownership\b",
    r"\bsole(?:ly)? responsible\b",
    r"\bfirst engineer\b",
    r"\breported (?:directly )?to\b",
)

# Capitalised words that look like technologies to the heuristic but are not,
# typically because they start a sentence or name a company or a month.
STOPWORD_CAPS: frozenset[str] = frozenset(
    """
the a an and or but for nor so yet at by in of on to with from into over
built designed created developed implemented improved reduced increased
migrated wrote added shipped delivered launched refactored rewrote
january february march april may june july august september october
november december mon tue wed thu fri sat sun
i we our my their his her its this that these those
""".split()
)
