/**
 * The concrete Fastify instance type for this app.
 *
 * Fastify's bare `FastifyInstance` default uses `FastifyBaseLogger`, but we
 * construct the server with a pino `Logger`. Under `exactOptionalPropertyTypes`
 * those two are not assignable, so passing the real instance to anything typed
 * as the default fails. Naming the concrete type once here keeps that knowledge
 * in a single file instead of spreading casts across every route module.
 */
import type { IncomingMessage, Server, ServerResponse } from "node:http";
import type { FastifyInstance } from "fastify";
import type { Logger } from "pino";

export type GatewayApp = FastifyInstance<Server, IncomingMessage, ServerResponse, Logger>;
