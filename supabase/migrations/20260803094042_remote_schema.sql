-- Migration unit 1: schema_changes
-- Transaction mode: transactional
-- Boundary reason: default

SET check_function_bodies = false;

DROP EXTENSION pg_net;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT DELETE, INSERT, SELECT, UPDATE ON TABLES TO anon;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, USAGE ON SEQUENCES TO anon;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON ROUTINES TO anon;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT DELETE, INSERT, SELECT, UPDATE ON TABLES TO authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, USAGE ON SEQUENCES TO authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON ROUTINES TO authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT DELETE, INSERT, SELECT, UPDATE ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, USAGE ON SEQUENCES TO service_role;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON ROUTINES TO service_role;

CREATE FUNCTION public.set_updated_at()
  RETURNS TRIGGER
  LANGUAGE plpgsql
  AS $function$
begin
  new.updated_at = now();
  return new;
end;
$function$;

GRANT ALL ON FUNCTION public.set_updated_at() TO anon;

GRANT ALL ON FUNCTION public.set_updated_at() TO authenticated;

GRANT ALL ON FUNCTION public.set_updated_at() TO service_role;

CREATE TABLE public.briefing_deliveries (
  id          uuid                     DEFAULT gen_random_uuid() NOT NULL,
  user_id     uuid                     NOT NULL,
  account_id  text,
  as_of       date,
  channel     text                     DEFAULT 'whatsapp'::text NOT NULL,
  status      text                     NOT NULL,
  provider_id text,
  error       text,
  created_at  timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.briefing_deliveries
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.briefing_deliveries
  ADD CONSTRAINT briefing_deliveries_pkey PRIMARY KEY (id);

ALTER TABLE public.briefing_deliveries
  ADD CONSTRAINT briefing_deliveries_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.briefing_deliveries TO anon;

GRANT ALL ON public.briefing_deliveries TO authenticated;

GRANT ALL ON public.briefing_deliveries TO service_role;

CREATE INDEX briefing_deliveries_user ON public.briefing_deliveries (user_id, created_at DESC);

CREATE POLICY "user reads own deliveries" ON public.briefing_deliveries
  FOR SELECT
  TO authenticated
  USING ((auth.uid() = user_id));

CREATE TABLE public.conversations (
  id         uuid                     DEFAULT gen_random_uuid() NOT NULL,
  user_id    uuid                     NOT NULL,
  title      text,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.conversations
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.conversations
  ADD CONSTRAINT conversations_pkey PRIMARY KEY (id);

ALTER TABLE public.conversations
  ADD CONSTRAINT conversations_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.conversations TO anon;

GRANT ALL ON public.conversations TO authenticated;

GRANT ALL ON public.conversations TO service_role;

CREATE INDEX conversations_user_updated ON public.conversations (user_id, updated_at DESC);

CREATE TRIGGER conversations_set_updated_at
  BEFORE UPDATE ON public.conversations
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

CREATE POLICY "user owns conversation" ON public.conversations
  TO authenticated
  USING ((auth.uid() = user_id))
  WITH CHECK ((auth.uid() = user_id));

CREATE TABLE public.ibkr_connections (
  user_id              uuid                     NOT NULL,
  flex_token_encrypted text                     NOT NULL,
  flex_query_id        text                     NOT NULL,
  whatsapp_number      text                     NOT NULL,
  opt_in               boolean                  DEFAULT true NOT NULL,
  status               text                     DEFAULT 'active'::text NOT NULL,
  created_at           timestamp with time zone DEFAULT now() NOT NULL,
  updated_at           timestamp with time zone DEFAULT now() NOT NULL,
  email_opt_in         boolean                  DEFAULT false NOT NULL
);

ALTER TABLE public.ibkr_connections
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.ibkr_connections
  ADD CONSTRAINT ibkr_connections_pkey PRIMARY KEY (user_id);

ALTER TABLE public.ibkr_connections
  ADD CONSTRAINT ibkr_connections_status_check CHECK (status = ANY (ARRAY['active'::text, 'paused'::text, 'revoked'::text]));

ALTER TABLE public.ibkr_connections
  ADD CONSTRAINT ibkr_connections_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.ibkr_connections TO anon;

GRANT ALL ON public.ibkr_connections TO authenticated;

GRANT ALL ON public.ibkr_connections TO service_role;

CREATE INDEX ibkr_connections_active ON public.ibkr_connections (opt_in, status);

CREATE TRIGGER ibkr_connections_set_updated_at
  BEFORE UPDATE ON public.ibkr_connections
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

CREATE POLICY "user owns ibkr connection" ON public.ibkr_connections
  TO authenticated
  USING ((auth.uid() = user_id))
  WITH CHECK ((auth.uid() = user_id));

CREATE TABLE public.messages (
  id              uuid                     DEFAULT gen_random_uuid() NOT NULL,
  conversation_id uuid                     NOT NULL,
  user_id         uuid                     NOT NULL,
  role            text                     NOT NULL,
  content         text,
  widgets         jsonb,
  created_at      timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.messages
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.messages
  ADD CONSTRAINT messages_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES public.conversations(id) ON DELETE CASCADE;

ALTER TABLE public.messages
  ADD CONSTRAINT messages_pkey PRIMARY KEY (id);

ALTER TABLE public.messages
  ADD CONSTRAINT messages_role_check CHECK (role = ANY (ARRAY['user'::text, 'assistant'::text]));

ALTER TABLE public.messages
  ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.messages TO anon;

GRANT ALL ON public.messages TO authenticated;

GRANT ALL ON public.messages TO service_role;

CREATE INDEX messages_conversation ON public.messages (conversation_id, created_at);

CREATE INDEX messages_user ON public.messages (user_id, created_at DESC);

CREATE POLICY "user owns message" ON public.messages
  TO authenticated
  USING ((auth.uid() = user_id))
  WITH CHECK ((auth.uid() = user_id));

CREATE TABLE public.pinned_widgets (
  id         uuid                     DEFAULT gen_random_uuid() NOT NULL,
  user_id    uuid                     NOT NULL,
  widget     jsonb                    NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.pinned_widgets
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.pinned_widgets
  ADD CONSTRAINT pinned_widgets_pkey PRIMARY KEY (id);

ALTER TABLE public.pinned_widgets
  ADD CONSTRAINT pinned_widgets_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.pinned_widgets TO anon;

GRANT ALL ON public.pinned_widgets TO authenticated;

GRANT ALL ON public.pinned_widgets TO service_role;

CREATE INDEX pinned_widgets_user ON public.pinned_widgets (user_id, created_at DESC);

CREATE POLICY "user owns pinned widget" ON public.pinned_widgets
  TO authenticated
  USING ((auth.uid() = user_id))
  WITH CHECK ((auth.uid() = user_id));

CREATE TABLE public.published_briefs (
  token      text                     NOT NULL,
  user_id    uuid,
  account_id text,
  as_of      date,
  body       text                     NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  expires_at timestamp with time zone DEFAULT (now() + '7 days'::interval) NOT NULL,
  chart_data jsonb
);

ALTER TABLE public.published_briefs
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.published_briefs
  ADD CONSTRAINT published_briefs_pkey PRIMARY KEY (token);

ALTER TABLE public.published_briefs
  ADD CONSTRAINT published_briefs_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.published_briefs TO anon;

GRANT ALL ON public.published_briefs TO authenticated;

GRANT ALL ON public.published_briefs TO service_role;

CREATE INDEX published_briefs_user ON public.published_briefs (user_id, created_at DESC);

CREATE INDEX published_briefs_expiry ON public.published_briefs (expires_at);

CREATE TABLE public.user_profiles (
  user_id      uuid                     NOT NULL,
  display_name text,
  created_at   timestamp with time zone DEFAULT now() NOT NULL,
  updated_at   timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.user_profiles
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.user_profiles
  ADD CONSTRAINT user_profiles_pkey PRIMARY KEY (user_id);

ALTER TABLE public.user_profiles
  ADD CONSTRAINT user_profiles_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

GRANT ALL ON public.user_profiles TO anon;

GRANT ALL ON public.user_profiles TO authenticated;

GRANT ALL ON public.user_profiles TO service_role;

CREATE TRIGGER user_profiles_set_updated_at
  BEFORE UPDATE ON public.user_profiles
  FOR EACH ROW
  EXECUTE FUNCTION public.set_updated_at();

CREATE POLICY "user owns profile" ON public.user_profiles
  TO authenticated
  USING ((auth.uid() = user_id))
  WITH CHECK ((auth.uid() = user_id));

CREATE TABLE public.waitlist_signups (
  id         uuid                     DEFAULT gen_random_uuid() NOT NULL,
  email      text                     NOT NULL,
  source     text,
  created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.waitlist_signups
  ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.waitlist_signups
  ADD CONSTRAINT waitlist_signups_pkey PRIMARY KEY (id);

GRANT ALL ON public.waitlist_signups TO anon;

GRANT ALL ON public.waitlist_signups TO authenticated;

GRANT ALL ON public.waitlist_signups TO service_role;

CREATE UNIQUE INDEX waitlist_signups_email_uniq ON public.waitlist_signups (lower(email));

CREATE POLICY "anon can join waitlist" ON public.waitlist_signups
  FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);
