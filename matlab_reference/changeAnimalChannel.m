function endTime = changeAnimalChannel(...
    channelNum,...
    startTime,...
    startChannelTime)
%% Constants
NUM_OF_CHANNELS = 16;

%% Variables
endTime = [];
endChannelTime = toc(startChannelTime);	% capture elapsed time
endChannelTime_min = endChannelTime / 60;
endChannelTime_h = endChannelTime_min / 60;
if endChannelTime_min < 1
    endChannelTime_use = sprintf('%f s',endChannelTime);
elseif endChannelTime_h < 1
    endChannelTime_use = sprintf('%f min',endChannelTime_min);
else
    endChannelTime_use = sprintf('%f h',endChannelTime_h);
end
nextChannelNum = channelNum + 1;        % next channel number
prompt1 = sprintf('Channel %d completed in %s\n',channelNum,endChannelTime_use);	% elapsed time
prompt2 = sprintf('Next is Channel %d.\n',nextChannelNum);                   	% next chnnel

%% Function
PS_StopStimChannel(1,channelNum); % stop stimulating channel
if channelNum < NUM_OF_CHANNELS          	% more channels
    fprintf(prompt1);
    fprintf(prompt2);
%     fprintf('Moving onto next channel...\n\n');% automatically change channel
    fprintf('\n');
else % on last channel
    fprintf(prompt1);
    endTime = toc(startTime);
    endTime_min = endTime / 60;
    endTime_h = endTime_min / 60;
    if endTime_min < 1
        endTime_use = sprintf('%f s',endTime);
    elseif endTime_h < 1
        endTime_use = sprintf('%f min',endTime_min);
    else
        endTime_use = sprintf('%f h',endTime_h);
    end
    fprintf('\nAll stimulations completed in %s\n',endTime_use);
end

end