export type Option={id:string;label:string;scores:Record<string,number>;image_url?:string}
export type Question={id:string;position:number;phase:string;kind:string;text:string;variables:string[];options:Option[];note?:string;active:boolean;image_url?:string}
export type Next={done:boolean;question?:Question;progress:number;answered?:number;total?:number;result?:any;context?:{product?:string;platform?:string;theme?:string}|null}
export type Manifest={version:string;questions:Question[];branches:{source_question:string;target_question:string;operator:string;expected_value:string;action:string}[]}
export type Dashboard={stats:{total:number;completed:number;active:number};respondents:any[];variables:any[];questions:Question[];last_import:{value:string}}
