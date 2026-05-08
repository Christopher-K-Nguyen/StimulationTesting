function filepath_tif = saveFig_Tek(File,fig)
%% File Names
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);

% subject = File.Subject;
% subject_fix = strrep(subject,' ','_');
savePath = File.Path;
name = File.Data(captureNum).Name;

% Starting filename
filename_tif = [name '.tif'];  % filename for .tif file (char)
filepath_tif = fullfile(savePath,filename_tif);
if isfile(filepath_tif)
    delete(filepath_tif);
end

% Save figure
% fig = File.Data(captureNum).Figure;
if ishandle(fig)
    fprintf('Saving figure...');
    startTime = tic;    
    print(fig,'-dtiff',filepath_tif,'-r400');
    [endTime,unit] = getEndTime(startTime);
    fprintf('"%s" (%.2f %s)\n',filename_tif,endTime,unit);   % .tif file saved
else
    filepath_tif = [];
end

end