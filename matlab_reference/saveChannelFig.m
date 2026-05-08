function filepath_tif = saveChannelFig(...
    File,...
    channelNum,...
    isBad,...
    filename,...
    filepath)
%% File Names
% Starting filename
filename_char = sprintf('%s_CH%02d',... % charge per phase
filename,channelNum);   
if isBad
    filename_char = append(filename_char,'_BAD');
end
    
% Save figure
Capture = File.Data(channelNum).Capture;
captureNum = length(Capture);
vtPlot = File.Data(channelNum).Capture(captureNum).Figure;
if ishandle(vtPlot)
    fprintf('Saving figure...');
    startTime = tic;
    fprintf('Channel %d...',channelNum);
    filename_tif = sprintf('%s.tif',filename_char);  % filename for .tif file (char)
    filepath_tif = fullfile(filepath,filename_tif);
    print(vtPlot,'-dtiff',filepath_tif,'-r300');
    endTime = toc(startTime);
    fprintf('"%s" (%.3f s)\n',filename_tif,endTime);   % .tif file saved
else
    filepath_tif = [];
end

end