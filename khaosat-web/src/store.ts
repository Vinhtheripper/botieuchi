import {configureStore,createSlice,PayloadAction} from '@reduxjs/toolkit'
import {createApi,fetchBaseQuery} from '@reduxjs/toolkit/query/react'
import type {Dashboard,Next} from './types'
import {API_BASE_URL} from './config'

const session=createSlice({name:'session',initialState:{id:localStorage.getItem('survey_session') as string|null},reducers:{setSession(s,a:PayloadAction<string|null>){s.id=a.payload;a.payload?localStorage.setItem('survey_session',a.payload):localStorage.removeItem('survey_session')}}})
export const {setSession}=session.actions
export const api=createApi({reducerPath:'api',baseQuery:fetchBaseQuery({baseUrl:API_BASE_URL,prepareHeaders(h){const token=sessionStorage.getItem('admin_token');if(token)h.set('authorization',`Bearer ${token}`);return h}}),tagTypes:['Dashboard','Next'],endpoints:b=>({
 start:b.mutation<{id:string},{name:string;email?:string;consent:boolean}>({query:body=>({url:'sessions',method:'POST',body})}),
 next:b.query<Next,string>({query:id=>`sessions/${id}/next`,providesTags:['Next']}),
 answer:b.mutation<{ok:boolean;replayed?:boolean;next?:Next},{id:string;question_id:string;option_id:string;value?:string}>({query:({id,...body})=>({url:`sessions/${id}/answers`,method:'POST',body}),async onQueryStarted({id},{dispatch,queryFulfilled}){const{data}=await queryFulfilled;if(data.next)dispatch(api.util.updateQueryData('next',id,draft=>Object.assign(draft,data.next)));else dispatch(api.util.invalidateTags(['Next']))}}),
 dashboard:b.query<Dashboard,void>({query:()=>`admin/dashboard`,providesTags:['Dashboard']}),
 sheets:b.query<any[],void>({query:()=>`admin/sheets`}),
 reimport:b.mutation<any,void>({query:()=>({url:'admin/import',method:'POST'}),invalidatesTags:['Dashboard']}),
 updateQuestion:b.mutation<any,{id:string;text:string;active:boolean}>({query:({id,...body})=>({url:`admin/questions/${id}`,method:'PATCH',body}),invalidatesTags:['Dashboard']}),
 createQuestion:b.mutation<any,any>({query:body=>({url:'admin/questions',method:'POST',body}),invalidatesTags:['Dashboard']}),
 deleteQuestion:b.mutation<any,string>({query:id=>({url:`admin/questions/${id}`,method:'DELETE'}),invalidatesTags:['Dashboard']}),
 addSheetRow:b.mutation<any,{name:string;values:any[]}>({query:({name,...body})=>({url:`admin/sheets/${encodeURIComponent(name)}/rows`,method:'POST',body}),invalidatesTags:['Dashboard']}),
 heuristic:b.query<any,void>({query:()=>`admin/heuristic`}),
 saveHeuristic:b.mutation<any,{weights:Record<string,number>}>({query:body=>({url:'admin/heuristic',method:'PUT',body})}),
 insights:b.query<any,void>({query:()=>`admin/insights`,providesTags:['Dashboard']}),
 analytics:b.query<any,void>({query:()=>`admin/analytics`,providesTags:['Dashboard']}),
 respondentAnswers:b.query<any,string>({query:id=>`admin/respondents/${id}/answers`}),
 settings:b.query<any,void>({query:()=>`admin/settings`,providesTags:['Dashboard']}),
 setPilot:b.mutation<any,{enabled:boolean}>({query:body=>({url:'admin/settings/pilot',method:'PUT',body}),invalidatesTags:['Dashboard']}),
 branches:b.query<any[],void>({query:()=>`admin/branches`,providesTags:['Dashboard']}),
 createBranch:b.mutation<any,any>({query:body=>({url:'admin/branches',method:'POST',body}),invalidatesTags:['Dashboard']}),
 deleteBranch:b.mutation<any,number>({query:id=>({url:`admin/branches/${id}`,method:'DELETE'}),invalidatesTags:['Dashboard']}),
 audit:b.query<any[],void>({query:()=>`admin/audit`}),
 adminUsers:b.query<any[],void>({query:()=>`admin/users`,providesTags:['Dashboard']}),
 createAdminUser:b.mutation<any,{username:string;password:string;role:string}>({query:body=>({url:'admin/users',method:'POST',body}),invalidatesTags:['Dashboard']}),
 notes:b.query<any[],void>({query:()=>`admin/notes`,providesTags:['Dashboard']}),
 createNote:b.mutation<any,{title:string;content:string}>({query:body=>({url:'admin/notes',method:'POST',body}),invalidatesTags:['Dashboard']}),
 deleteNote:b.mutation<any,number>({query:id=>({url:`admin/notes/${id}`,method:'DELETE'}),invalidatesTags:['Dashboard']}),
})})
export const {useStartMutation,useNextQuery,useAnswerMutation,useDashboardQuery,useSheetsQuery,useReimportMutation,useUpdateQuestionMutation,useCreateQuestionMutation,useDeleteQuestionMutation,useAddSheetRowMutation,useHeuristicQuery,useSaveHeuristicMutation,useInsightsQuery,useAnalyticsQuery,useRespondentAnswersQuery,useSettingsQuery,useSetPilotMutation,useBranchesQuery,useCreateBranchMutation,useDeleteBranchMutation,useAuditQuery,useAdminUsersQuery,useCreateAdminUserMutation,useNotesQuery,useCreateNoteMutation,useDeleteNoteMutation}=api
export const store=configureStore({reducer:{session:session.reducer,[api.reducerPath]:api.reducer},middleware:g=>g().concat(api.middleware)})
export type RootState=ReturnType<typeof store.getState>
