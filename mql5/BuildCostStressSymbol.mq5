#property copyright "mt5-ea-validator"
#property version   "1.00"
#property script_show_inputs

input string InpSourceSymbol = "XAUUSD";
input string InpCustomSymbol = "XAUUSD_TCS0";
input string InpCustomGroup = "MT5EAValidator\\TransactionCost";
input string InpFromDate = "2024.01.01";
input string InpToDateExclusive = "2026.07.01";
input int InpStressPoints = 0;
input string InpOutputFile = "mt5_ea_validator\\transaction_cost\\manifest.txt";

#define DAY_MILLISECONDS 86400000
#define SPREAD_HISTOGRAM_MAX 100000

ulong g_source_hash = 14695981039346656037;
ulong g_output_hash = 14695981039346656037;
ulong g_persisted_hash = 14695981039346656037;
ulong g_source_ticks = 0;
ulong g_written_ticks = 0;
ulong g_persisted_ticks = 0;
ulong g_first_time_msc = 0;
ulong g_last_time_msc = 0;
ulong g_readback_mismatch_from_msc = 0;
int g_readback_expected_day_count = 0;
int g_readback_actual_day_count = 0;
ulong g_source_m1_hash = 14695981039346656037;
ulong g_output_m1_hash = 14695981039346656037;
ulong g_persisted_m1_hash = 14695981039346656037;
ulong g_source_m1_bars = 0;
ulong g_written_m1_bars = 0;
ulong g_persisted_m1_bars = 0;
ulong g_first_m1_time = 0;
ulong g_last_m1_time = 0;
ulong g_m1_readback_mismatch_from = 0;
int g_m1_readback_expected_day_count = 0;
int g_m1_readback_actual_day_count = 0;
ulong g_spread_sum_points = 0;
int g_spread_min_points = INT_MAX;
int g_spread_max_points = 0;
ulong g_spread_histogram[];
int g_digits = 0;
double g_point = 0.0;
double g_tick_size = 0.0;

ulong MixUlong(ulong hash_value, ulong value)
  {
   for(int shift=0; shift<64; shift+=8)
     {
      hash_value^=(value>>shift)&0xFF;
      hash_value*=1099511628211;
     }
   return(hash_value);
  }

ulong MixTick(ulong hash_value,const MqlTick &tick)
  {
   hash_value=MixUlong(hash_value,(ulong)tick.time_msc);
   hash_value=MixUlong(hash_value,(ulong)MathRound(tick.bid/g_point));
   hash_value=MixUlong(hash_value,(ulong)MathRound(tick.ask/g_point));
   return(hash_value);
  }

ulong MixRate(ulong hash_value,const MqlRates &rate)
  {
   hash_value=MixUlong(hash_value,(ulong)rate.time);
   hash_value=MixUlong(hash_value,(ulong)MathRound(rate.open/g_point));
   hash_value=MixUlong(hash_value,(ulong)MathRound(rate.high/g_point));
   hash_value=MixUlong(hash_value,(ulong)MathRound(rate.low/g_point));
   hash_value=MixUlong(hash_value,(ulong)MathRound(rate.close/g_point));
   hash_value=MixUlong(hash_value,(ulong)rate.tick_volume);
   hash_value=MixUlong(hash_value,(ulong)rate.spread);
   hash_value=MixUlong(hash_value,(ulong)rate.real_volume);
   return(hash_value);
  }

int SpreadPercentile(const double percentile)
  {
   if(g_source_ticks==0)
      return(0);
   ulong target=(ulong)MathCeil((double)g_source_ticks*percentile);
   ulong cumulative=0;
   int size=ArraySize(g_spread_histogram);
   for(int index=0; index<size; index++)
     {
      cumulative+=g_spread_histogram[index];
      if(cumulative>=target)
         return(index);
     }
   return(size-1);
  }

bool WriteManifest(const string status,const string error_message,
                   const ulong from_msc,const ulong to_msc_exclusive)
  {
   int handle=FileOpen(InpOutputFile,FILE_WRITE|FILE_TXT|FILE_ANSI,CP_UTF8);
   if(handle==INVALID_HANDLE)
     {
      PrintFormat("Cannot write manifest '%s': error=%d",InpOutputFile,GetLastError());
      return(false);
     }
   string escaped_error=error_message;
   StringReplace(escaped_error,"\r"," ");
   StringReplace(escaped_error,"\n"," ");
   FileWrite(handle,"schema_version=2");
   FileWrite(handle,"status="+status);
   FileWrite(handle,"error="+escaped_error);
   FileWrite(handle,"source_symbol="+InpSourceSymbol);
   FileWrite(handle,"custom_symbol="+InpCustomSymbol);
   FileWrite(handle,"custom_group="+InpCustomGroup);
   FileWrite(handle,"from_msc="+StringFormat("%I64u",from_msc));
   FileWrite(handle,"to_msc_exclusive="+StringFormat("%I64u",to_msc_exclusive));
   FileWrite(handle,"digits="+IntegerToString(g_digits));
   FileWrite(handle,"point="+DoubleToString(g_point,g_digits+2));
   FileWrite(handle,"tick_size="+DoubleToString(g_tick_size,g_digits+2));
   FileWrite(handle,"stress_points="+IntegerToString(InpStressPoints));
   FileWrite(handle,"stress_price="+DoubleToString(InpStressPoints*g_point,g_digits));
   FileWrite(handle,"source_tick_count="+StringFormat("%I64u",g_source_ticks));
   FileWrite(handle,"written_tick_count="+StringFormat("%I64u",g_written_ticks));
   FileWrite(handle,"persisted_tick_count="+StringFormat("%I64u",g_persisted_ticks));
   FileWrite(handle,"first_time_msc="+StringFormat("%I64u",g_first_time_msc));
   FileWrite(handle,"last_time_msc="+StringFormat("%I64u",g_last_time_msc));
   FileWrite(handle,"source_tick_audit_fnv1a64="+StringFormat("%016I64X",g_source_hash));
   FileWrite(handle,"output_tick_audit_fnv1a64="+StringFormat("%016I64X",g_output_hash));
   FileWrite(handle,"persisted_tick_audit_fnv1a64="+StringFormat("%016I64X",g_persisted_hash));
   FileWrite(handle,"readback_mismatch_from_msc="+
             StringFormat("%I64u",g_readback_mismatch_from_msc));
   FileWrite(handle,"readback_expected_day_count="+
             IntegerToString(g_readback_expected_day_count));
   FileWrite(handle,"readback_actual_day_count="+
             IntegerToString(g_readback_actual_day_count));
   FileWrite(handle,"source_m1_bar_count="+StringFormat("%I64u",g_source_m1_bars));
   FileWrite(handle,"written_m1_bar_count="+StringFormat("%I64u",g_written_m1_bars));
   FileWrite(handle,"persisted_m1_bar_count="+StringFormat("%I64u",g_persisted_m1_bars));
   FileWrite(handle,"first_m1_time="+StringFormat("%I64u",g_first_m1_time));
   FileWrite(handle,"last_m1_time="+StringFormat("%I64u",g_last_m1_time));
   FileWrite(handle,"source_m1_audit_fnv1a64="+StringFormat("%016I64X",g_source_m1_hash));
   FileWrite(handle,"output_m1_audit_fnv1a64="+StringFormat("%016I64X",g_output_m1_hash));
   FileWrite(handle,"persisted_m1_audit_fnv1a64="+StringFormat("%016I64X",g_persisted_m1_hash));
   FileWrite(handle,"m1_readback_mismatch_from="+
             StringFormat("%I64u",g_m1_readback_mismatch_from));
   FileWrite(handle,"m1_readback_expected_day_count="+
             IntegerToString(g_m1_readback_expected_day_count));
   FileWrite(handle,"m1_readback_actual_day_count="+
             IntegerToString(g_m1_readback_actual_day_count));
   if(g_source_ticks>0)
     {
      FileWrite(handle,"source_spread_min_points="+IntegerToString(g_spread_min_points));
      FileWrite(handle,"source_spread_mean_points="+
                DoubleToString((double)g_spread_sum_points/(double)g_source_ticks,4));
      FileWrite(handle,"source_spread_p50_points="+IntegerToString(SpreadPercentile(0.50)));
      FileWrite(handle,"source_spread_p95_points="+IntegerToString(SpreadPercentile(0.95)));
      FileWrite(handle,"source_spread_p99_points="+IntegerToString(SpreadPercentile(0.99)));
      FileWrite(handle,"source_spread_max_points="+IntegerToString(g_spread_max_points));
     }
   FileClose(handle);
   return(true);
  }

bool CopyDayTicks(const ulong from_msc,const ulong to_msc,MqlTick &ticks[],string &error_message)
  {
   for(int attempt=1; attempt<=5; attempt++)
     {
      ResetLastError();
      int copied=CopyTicksRange(InpSourceSymbol,ticks,COPY_TICKS_ALL,from_msc,to_msc);
      int error_code=GetLastError();
      if(copied>=0 && error_code==0)
         return(true);
      PrintFormat("CopyTicksRange attempt=%d from=%I64u to=%I64u copied=%d error=%d",
                  attempt,from_msc,to_msc,copied,error_code);
      Sleep(1000);
     }
   error_message=StringFormat("CopyTicksRange failed from=%I64u to=%I64u error=%d",
                              from_msc,to_msc,GetLastError());
   return(false);
  }

bool ReadBackDayTicks(const ulong from_msc,const ulong to_msc,
                      MqlTick &ticks[],string &error_message)
  {
   for(int attempt=1; attempt<=5; attempt++)
     {
      ResetLastError();
      int copied=CopyTicksRange(InpCustomSymbol,ticks,COPY_TICKS_ALL,from_msc,to_msc);
      int error_code=GetLastError();
      if(copied>=0 && error_code==0)
         return(true);
      PrintFormat("Custom read-back attempt=%d from=%I64u to=%I64u copied=%d error=%d",
                  attempt,from_msc,to_msc,copied,error_code);
      Sleep(1000);
     }
   error_message=StringFormat("Custom read-back failed from=%I64u to=%I64u error=%d",
                              from_msc,to_msc,GetLastError());
   return(false);
  }

bool ProcessTicks(const ulong from_msc,const ulong to_msc_exclusive,string &error_message)
  {
   double stress_price=InpStressPoints*g_point;
   for(ulong day_from=from_msc; day_from<to_msc_exclusive; day_from+=DAY_MILLISECONDS)
     {
      ulong day_to=day_from+DAY_MILLISECONDS-1;
      if(day_to>=to_msc_exclusive)
         day_to=to_msc_exclusive-1;
      MqlTick ticks[];
      if(!CopyDayTicks(day_from,day_to,ticks,error_message))
         return(false);
      int copied=ArraySize(ticks);
      if(copied==0)
         continue;
      for(int index=0; index<copied; index++)
        {
         MqlTick source_tick=ticks[index];
         if(g_first_time_msc==0)
            g_first_time_msc=(ulong)source_tick.time_msc;
         g_last_time_msc=(ulong)source_tick.time_msc;
         g_source_hash=MixTick(g_source_hash,source_tick);
         int spread_points=(int)MathRound((source_tick.ask-source_tick.bid)/g_point);
         if(spread_points<0)
           {
            error_message=StringFormat("Negative source spread at %I64d",source_tick.time_msc);
            return(false);
           }
         g_spread_min_points=MathMin(g_spread_min_points,spread_points);
         g_spread_max_points=MathMax(g_spread_max_points,spread_points);
         g_spread_sum_points+=(ulong)spread_points;
         int histogram_index=MathMin(spread_points,SPREAD_HISTOGRAM_MAX);
         g_spread_histogram[histogram_index]++;

         ticks[index].bid=NormalizeDouble(source_tick.bid-stress_price,g_digits);
         ticks[index].ask=NormalizeDouble(source_tick.ask+stress_price,g_digits);
         if(ticks[index].bid<=0.0 || ticks[index].ask<ticks[index].bid)
           {
            error_message=StringFormat("Invalid transformed quote at %I64d bid=%s ask=%s",
                                       source_tick.time_msc,
                                       DoubleToString(ticks[index].bid,g_digits),
                                       DoubleToString(ticks[index].ask,g_digits));
            return(false);
           }
         g_output_hash=MixTick(g_output_hash,ticks[index]);
        }
      g_source_ticks+=(ulong)copied;
      ResetLastError();
      int written=CustomTicksReplace(InpCustomSymbol,(long)day_from,(long)day_to,ticks,(uint)copied);
      if(written!=copied)
        {
         error_message=StringFormat("CustomTicksReplace mismatch from=%I64u copied=%d written=%d error=%d",
                                    day_from,copied,written,GetLastError());
         return(false);
        }
      g_written_ticks+=(ulong)written;

      MqlTick persisted[];
      if(!ReadBackDayTicks(day_from,day_to,persisted,error_message))
         return(false);
      int persisted_count=ArraySize(persisted);
      g_persisted_ticks+=(ulong)persisted_count;
      if(persisted_count!=copied)
        {
         g_readback_mismatch_from_msc=day_from;
         g_readback_expected_day_count=copied;
         g_readback_actual_day_count=persisted_count;
         error_message=StringFormat(
            "Persisted tick count mismatch from=%I64u expected=%d actual=%d",
            day_from,copied,persisted_count);
         return(false);
        }
      for(int index=0; index<persisted_count; index++)
        {
         g_persisted_hash=MixTick(g_persisted_hash,persisted[index]);
         if(persisted[index].time_msc!=ticks[index].time_msc ||
            MathRound(persisted[index].bid/g_point)!=MathRound(ticks[index].bid/g_point) ||
            MathRound(persisted[index].ask/g_point)!=MathRound(ticks[index].ask/g_point))
           {
            g_readback_mismatch_from_msc=day_from;
            g_readback_expected_day_count=copied;
            g_readback_actual_day_count=persisted_count;
            error_message=StringFormat(
               "Persisted tick identity mismatch from=%I64u index=%d expected_time=%I64d actual_time=%I64d",
               day_from,index,ticks[index].time_msc,persisted[index].time_msc);
            return(false);
           }
        }
      PrintFormat("Processed day from=%I64u ticks=%d total=%I64u",
                  day_from,copied,g_written_ticks);
     }
   if(g_source_ticks==0)
     {
      error_message="No source ticks were returned";
      return(false);
     }
   if(g_source_ticks!=g_written_ticks)
     {
      error_message=StringFormat("Total tick mismatch source=%I64u written=%I64u",
                                 g_source_ticks,g_written_ticks);
      return(false);
     }
   if(g_written_ticks!=g_persisted_ticks || g_output_hash!=g_persisted_hash)
     {
      error_message=StringFormat(
         "Persisted tick audit mismatch written=%I64u persisted=%I64u output_hash=%016I64X persisted_hash=%016I64X",
         g_written_ticks,g_persisted_ticks,g_output_hash,g_persisted_hash);
      return(false);
     }
   return(true);
  }

bool CopyDayRates(const string symbol,const datetime from_time,const datetime to_time,
                  MqlRates &rates[],string &error_message)
  {
   for(int attempt=1; attempt<=5; attempt++)
     {
      ResetLastError();
      int copied=CopyRates(symbol,PERIOD_M1,from_time,to_time,rates);
      int error_code=GetLastError();
      if(copied>=0 && error_code==0)
         return(true);
      PrintFormat("CopyRates attempt=%d symbol=%s from=%I64d to=%I64d copied=%d error=%d",
                  attempt,symbol,from_time,to_time,copied,error_code);
      Sleep(1000);
     }
   error_message=StringFormat("CopyRates failed symbol=%s from=%I64d to=%I64d error=%d",
                              symbol,from_time,to_time,GetLastError());
   return(false);
  }

bool ProcessM1Rates(const ulong from_msc,const ulong to_msc_exclusive,string &error_message)
  {
   for(ulong day_from=from_msc; day_from<to_msc_exclusive; day_from+=DAY_MILLISECONDS)
     {
      ulong day_to=day_from+DAY_MILLISECONDS-1;
      if(day_to>=to_msc_exclusive)
         day_to=to_msc_exclusive-1;
      datetime from_time=(datetime)(day_from/1000);
      datetime to_time=(datetime)(day_to/1000);
      MqlRates source_rates[];
      if(!CopyDayRates(InpSourceSymbol,from_time,to_time,source_rates,error_message))
         return(false);
      int source_count=ArraySize(source_rates);
      if(source_count==0)
         continue;
      MqlRates output_rates[];
      if(ArrayCopy(output_rates,source_rates)!=source_count)
        {
         error_message=StringFormat("Cannot copy source M1 rates from=%I64d count=%d",
                                    from_time,source_count);
         return(false);
        }
      double stress_price=InpStressPoints*g_point;
      for(int index=0; index<source_count; index++)
        {
         if(g_first_m1_time==0)
            g_first_m1_time=(ulong)source_rates[index].time;
         g_last_m1_time=(ulong)source_rates[index].time;
         g_source_m1_hash=MixRate(g_source_m1_hash,source_rates[index]);
         long stressed_spread=(long)source_rates[index].spread+2*(long)InpStressPoints;
         if(stressed_spread<0 || stressed_spread>INT_MAX)
           {
            error_message=StringFormat(
               "Stressed M1 spread is invalid from=%I64d index=%d spread=%I64d",
               from_time,index,stressed_spread);
            return(false);
           }
         output_rates[index].open=NormalizeDouble(source_rates[index].open-stress_price,g_digits);
         output_rates[index].high=NormalizeDouble(source_rates[index].high-stress_price,g_digits);
         output_rates[index].low=NormalizeDouble(source_rates[index].low-stress_price,g_digits);
         output_rates[index].close=NormalizeDouble(source_rates[index].close-stress_price,g_digits);
         output_rates[index].spread=(int)stressed_spread;
         if(output_rates[index].low<=0)
           {
            error_message=StringFormat(
               "Stressed M1 price is invalid from=%I64d index=%d low=%.*f",
               from_time,index,g_digits,output_rates[index].low);
            return(false);
           }
         g_output_m1_hash=MixRate(g_output_m1_hash,output_rates[index]);
        }
      g_source_m1_bars+=(ulong)source_count;

      ResetLastError();
      int written=CustomRatesReplace(InpCustomSymbol,from_time,to_time,output_rates,(uint)source_count);
      if(written!=source_count)
        {
         error_message=StringFormat(
            "CustomRatesReplace mismatch from=%I64d copied=%d written=%d error=%d",
            from_time,source_count,written,GetLastError());
         return(false);
        }
      g_written_m1_bars+=(ulong)written;

      MqlRates persisted_rates[];
      if(!CopyDayRates(InpCustomSymbol,from_time,to_time,persisted_rates,error_message))
         return(false);
      int persisted_count=ArraySize(persisted_rates);
      g_persisted_m1_bars+=(ulong)persisted_count;
      if(persisted_count!=source_count)
        {
         g_m1_readback_mismatch_from=(ulong)from_time;
         g_m1_readback_expected_day_count=source_count;
         g_m1_readback_actual_day_count=persisted_count;
         error_message=StringFormat(
            "Persisted M1 count mismatch from=%I64d expected=%d actual=%d",
            from_time,source_count,persisted_count);
         return(false);
        }
      for(int index=0; index<persisted_count; index++)
        {
         g_persisted_m1_hash=MixRate(g_persisted_m1_hash,persisted_rates[index]);
         if(output_rates[index].time!=persisted_rates[index].time ||
            MathRound(output_rates[index].open/g_point)!=MathRound(persisted_rates[index].open/g_point) ||
            MathRound(output_rates[index].high/g_point)!=MathRound(persisted_rates[index].high/g_point) ||
            MathRound(output_rates[index].low/g_point)!=MathRound(persisted_rates[index].low/g_point) ||
            MathRound(output_rates[index].close/g_point)!=MathRound(persisted_rates[index].close/g_point) ||
            output_rates[index].tick_volume!=persisted_rates[index].tick_volume ||
            output_rates[index].spread!=persisted_rates[index].spread ||
            output_rates[index].real_volume!=persisted_rates[index].real_volume)
           {
            g_m1_readback_mismatch_from=(ulong)from_time;
            g_m1_readback_expected_day_count=source_count;
            g_m1_readback_actual_day_count=persisted_count;
            error_message=StringFormat(
               "Persisted M1 identity mismatch from=%I64d index=%d expected_time=%I64d actual_time=%I64d",
               from_time,index,output_rates[index].time,persisted_rates[index].time);
            return(false);
           }
        }
      PrintFormat("Processed M1 day from=%I64u bars=%d total=%I64u",
                  day_from,source_count,g_written_m1_bars);
     }
   if(g_source_m1_bars==0)
     {
      error_message="No source M1 bars were returned";
      return(false);
     }
   if(g_source_m1_bars!=g_written_m1_bars ||
      g_written_m1_bars!=g_persisted_m1_bars ||
      g_output_m1_hash!=g_persisted_m1_hash)
     {
      error_message=StringFormat(
         "Persisted M1 audit mismatch source=%I64u written=%I64u persisted=%I64u output_hash=%016I64X persisted_hash=%016I64X",
         g_source_m1_bars,g_written_m1_bars,g_persisted_m1_bars,
         g_output_m1_hash,g_persisted_m1_hash);
      return(false);
     }
   return(true);
  }

void OnStart()
  {
   ArrayResize(g_spread_histogram,SPREAD_HISTOGRAM_MAX+1);
   ArrayInitialize(g_spread_histogram,0);
   string error_message="";
   datetime from_time=StringToTime(InpFromDate);
   datetime to_time=StringToTime(InpToDateExclusive);
   ulong from_msc=(ulong)from_time*1000;
   ulong to_msc_exclusive=(ulong)to_time*1000;

   if(InpStressPoints<0)
      error_message="InpStressPoints must be non-negative";
   else if(from_time<=0 || to_time<=from_time)
      error_message="Invalid date interval";
   else if(InpCustomSymbol==InpSourceSymbol)
      error_message="Custom symbol must differ from source symbol";
   else if(!SymbolSelect(InpSourceSymbol,true))
      error_message=StringFormat("Cannot select source symbol: error=%d",GetLastError());

   if(error_message=="")
     {
      g_digits=(int)SymbolInfoInteger(InpSourceSymbol,SYMBOL_DIGITS);
      g_point=SymbolInfoDouble(InpSourceSymbol,SYMBOL_POINT);
      g_tick_size=SymbolInfoDouble(InpSourceSymbol,SYMBOL_TRADE_TICK_SIZE);
      if(g_digits<0 || g_point<=0.0 || g_tick_size<=0.0)
         error_message="Invalid source symbol specification";
     }

   if(error_message=="")
     {
      ResetLastError();
      if(!CustomSymbolCreate(InpCustomSymbol,InpCustomGroup,InpSourceSymbol))
         error_message=StringFormat("CustomSymbolCreate failed; refusing to overwrite error=%d",
                                    GetLastError());
      else if(!SymbolSelect(InpCustomSymbol,true))
         error_message=StringFormat("Cannot select custom symbol: error=%d",GetLastError());
     }

   bool success=false;
   if(error_message=="")
      success=ProcessTicks(from_msc,to_msc_exclusive,error_message);
   if(success)
      success=ProcessM1Rates(from_msc,to_msc_exclusive,error_message);
   WriteManifest(success ? "success" : "failed",error_message,from_msc,to_msc_exclusive);
   if(success)
      PrintFormat("COST_STRESS_SYMBOL_SUCCESS symbol=%s ticks=%I64u stress_points=%d",
                  InpCustomSymbol,g_written_ticks,InpStressPoints);
   else
      PrintFormat("COST_STRESS_SYMBOL_FAILED symbol=%s error=%s",
                  InpCustomSymbol,error_message);
  }
