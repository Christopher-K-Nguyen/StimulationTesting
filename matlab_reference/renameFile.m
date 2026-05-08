function hasPattern = renameFile(file,patternOld,patternNew)
%% Function
hasPattern = contains(file,patternOld);
if hasPattern == true
    [~,name,ext] = fileparts(file);
    filename = append(name,ext);
    fprintf('%s...',filename);
    startTime = tic;
    fileNew = strrep(file,patternOld,patternNew);
    movefile(file,fileNew,'f');
    [~,filename_new,ext] = fileparts(fileNew);
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s%s (%.3f %s)\n',filename_new,ext,endTime,unit);
end

end